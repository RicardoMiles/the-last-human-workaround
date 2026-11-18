#requires -Version 5.1
<#
One-time workaround for Kiro Crew 0.12.2 / bundled CPython 3.12.16.
See README.md for scope, machine-wide long-path change and validation limits.
Dot-source this file to load its functions without running the repair.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$CrewInstallDir,
    [string]$CrewHome,
    [string]$RestoreLongPathsFrom
)

function Test-IsWindows {
    return [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-RunningCrewProcesses {
    param([Parameter(Mandatory=$true)][string]$InstallDir)
    $prefix = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\') + '\'
    foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction Stop)) {
        $path = [string]$process.ExecutablePath
        $command = [string]$process.CommandLine
        $name = [string]$process.Name
        $inBundle = $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
        $namedCrew = $name -match '^(kiro[ -]?crew|kirocrew-backend)(\.exe)?$'
        $pythonCrew = ($name -match '^python(w)?(\.exe)?$') -and
            ($command -match '(?i)(-m\s+kiro_crew\b|[\\/]kirocrew(?:-backend)?(?:\.exe|[\\/])|\bkiro_crew\.(?:gateway|_bootstrap)\b)')
        if ($inBundle -or $namedCrew -or $pythonCrew) {
            # Do not record full command lines: they can contain unrelated secrets.
            [pscustomobject]@{ Id=$process.ProcessId; Name=$name; ExecutablePath=$path }
        }
    }
}

function Get-LongPathsState {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
        [Microsoft.Win32.RegistryHive]::LocalMachine,
        [Microsoft.Win32.RegistryView]::Registry64)
    $key = $null
    try {
        $key = $base.OpenSubKey('SYSTEM\CurrentControlSet\Control\FileSystem', $false)
        if ($null -eq $key) { throw 'Windows FileSystem registry key was not found.' }
        $exists = @($key.GetValueNames()) -contains 'LongPathsEnabled'
        if (-not $exists) { return [pscustomobject]@{ exists=$false; kind=$null; value=$null } }
        $kind = $key.GetValueKind('LongPathsEnabled').ToString()
        $value = $key.GetValue('LongPathsEnabled', $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        return [pscustomobject]@{ exists=$true; kind=$kind; value=$value }
    } finally {
        if ($null -ne $key) { $key.Dispose() }
        $base.Dispose()
    }
}

function Assert-LongPathsState {
    param([Parameter(Mandatory=$true)]$State)
    if ($null -eq $State -or $State.exists -isnot [bool]) { throw 'Invalid registry backup: exists must be boolean.' }
    if (-not $State.exists) {
        if ($null -ne $State.kind -or $null -ne $State.value) { throw 'Invalid absent registry value.' }
        return
    }
    if ($State.kind -cne 'DWord' -or ($State.value -isnot [int] -and $State.value -isnot [long])) {
        throw 'Only an absent value or the expected DWORD LongPathsEnabled value is supported.'
    }
    if ([long]$State.value -lt [int]::MinValue -or [long]$State.value -gt [int]::MaxValue) {
        throw 'DWORD value is out of range.'
    }
}

function Set-LongPathsState {
    param([Parameter(Mandatory=$true)]$State)
    Assert-LongPathsState $State
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
        [Microsoft.Win32.RegistryHive]::LocalMachine,
        [Microsoft.Win32.RegistryView]::Registry64)
    $key = $null
    try {
        $key = $base.OpenSubKey('SYSTEM\CurrentControlSet\Control\FileSystem', $true)
        if ($null -eq $key) { throw 'Cannot open Windows FileSystem registry key for writing.' }
        if (-not $State.exists) { $key.DeleteValue('LongPathsEnabled', $false) }
        else {
            $key.SetValue('LongPathsEnabled', [int]$State.value, [Microsoft.Win32.RegistryValueKind]::DWord)
        }
        $key.Flush()
    } finally {
        if ($null -ne $key) { $key.Dispose() }
        $base.Dispose()
    }
}

function Read-LongPathsBackup {
    param([Parameter(Mandatory=$true)][string]$Path)
    $backup = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ($backup.schema -cne 'kirocrew-pptx-longpaths-backup/v1' -or
        -not [string]::Equals([string]$backup.computer, [string]$env:COMPUTERNAME, [StringComparison]::OrdinalIgnoreCase) -or
        $backup.registryPath -cne 'HKLM\SYSTEM\CurrentControlSet\Control\FileSystem' -or
        $backup.valueName -cne 'LongPathsEnabled') { throw 'Backup schema, computer or registry target does not match.' }
    Assert-LongPathsState $backup.original
    return $backup
}

function New-WorkaroundLogDirectory {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'LOCALAPPDATA is unavailable. Use the same Windows account that installed Crew.' }
    $root = Join-Path $env:LOCALAPPDATA 'TheLastHumanWorkaround\pptx-maker'
    $name = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
    $directory = Join-Path $root $name
    [void](New-Item -ItemType Directory -Path $directory -Force -ErrorAction Stop)
    return $directory
}

function Invoke-BundledPython {
    param(
        [Parameter(Mandatory=$true)][string]$Python,
        [Parameter(Mandatory=$true)][string]$CrewHome,
        [Parameter(Mandatory=$true)][ValidateSet('check','repair')][string]$Mode,
        [Parameter(Mandatory=$true)][string]$LogDir
    )
    $code = @'
import importlib.metadata, json, os, pathlib, platform, subprocess, sys, traceback
mode = os.environ['KC_PPTX_MODE']
result_path = pathlib.Path(os.environ['KC_PPTX_RESULT'])
result = {'schema': 1, 'mode': mode, 'ok': False}
try:
    expected_home = pathlib.Path(os.environ['KIROCREW_HOME']).resolve()
    version = importlib.metadata.version('kirocrew')
    if version != '0.12.2':
        raise RuntimeError('Supported kirocrew version is 0.12.2; found ' + version)
    if sys.version_info[:3] != (3, 12, 16) or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Supported bundled interpreter is CPython 3.12.16 x64; found ' + sys.version)
    from kiro_crew.config import paths as config_paths
    from kiro_crew.apps.builtins.pptx_maker.backend import provision, paths, engine_source
    if engine_source.ENGINE_TAG != 'v0.3.8' or engine_source.ENGINE_COMMIT != 'b7c7fcf0972b33a480f1b717a3de5bc288d53742':
        raise RuntimeError('The installed PPTX engine pin is outside this workaround scope.')
    if not callable(provision.provision) or provision.provision.__code__.co_argcount != 0:
        raise RuntimeError('The installed provision() interface is unsupported.')
    actual_home = config_paths.config_dir().resolve()
    app_root = paths.app_root().resolve()
    engine_root = paths.engine_root().resolve()
    if actual_home != expected_home or not app_root.is_relative_to(expected_home) or app_root == expected_home:
        raise RuntimeError('Crew app data does not resolve inside the requested Crew home.')
    if not engine_root.is_relative_to(app_root) or engine_root == app_root:
        raise RuntimeError('Engine data does not resolve inside the PPTX app data directory.')
    if not (actual_home / 'config.json').is_file():
        raise RuntimeError('Existing Crew config.json is required; this workaround will not create or replace it.')
    result.update(crew_version=version, python_version=platform.python_version(), engine_tag=engine_source.ENGINE_TAG,
                  engine_commit=engine_source.ENGINE_COMMIT, crew_home=str(actual_home), app_root=str(app_root),
                  engine_root=str(engine_root), engine_python=str(paths.engine_python()))
    if mode == 'repair':
        outcome = provision.provision()
        print(outcome.log, flush=True)
        result['provision_ok'] = bool(outcome.ok)
        result['provision_engine_tag'] = str(outcome.engine_tag)
        if not outcome.ok:
            raise RuntimeError('Crew provision() reported failure; read stdout and stderr logs for its cause.')
        engine_python = paths.engine_python()
        installed_tag = engine_source.installed_tag(engine_root)
        if not engine_python.is_file() or installed_tag != 'v0.3.8' or not engine_source.is_installed(engine_root):
            raise RuntimeError('Provision returned success but the pinned engine/interpreter verification failed.')
        probe = subprocess.run([str(engine_python), '-I', '-B', '-c',
                                'import sys,sdpm; assert sys.version_info[:3] == (3,12,16); print(sys.version)'],
                               capture_output=True, text=True, encoding='utf-8', errors='replace')
        print(probe.stdout, end='', flush=True)
        print(probe.stderr, end='', file=sys.stderr, flush=True)
        if probe.returncode != 0:
            raise RuntimeError('The installed engine interpreter/import probe failed.')
        result['installed_tag'] = installed_tag
    result['ok'] = True
except Exception as exc:
    result['error'] = str(exc)
    traceback.print_exc()
finally:
    print(json.dumps(result, indent=2), flush=True)
    result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
sys.exit(0 if result['ok'] else 1)
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
    $resultPath = Join-Path $LogDir ($Mode + '-result.json')
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Python
    $info.Arguments = '-I -B -u -c "import base64;exec(base64.b64decode(''' + $encoded + '''))"'
    $info.WorkingDirectory = Split-Path -Parent $Python
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [Text.Encoding]::UTF8
    $info.EnvironmentVariables['UV_PYTHON'] = $Python
    $info.EnvironmentVariables['KIROCREW_HOME'] = $CrewHome
    $info.EnvironmentVariables['KC_PPTX_MODE'] = $Mode
    $info.EnvironmentVariables['KC_PPTX_RESULT'] = $resultPath
    $info.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'The bundled Python process did not start.' }
        # Drain both streams asynchronously before waiting: warnings on stderr are not failures.
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
    } finally { $process.Dispose() }
    [IO.File]::WriteAllText((Join-Path $LogDir ($Mode + '-stdout.log')), $stdout, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText((Join-Path $LogDir ($Mode + '-stderr.log')), $stderr, [Text.Encoding]::UTF8)
    if ($stdout) { Write-Host $stdout }
    if ($stderr) { Write-Host $stderr -ForegroundColor DarkYellow }
    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { throw "Python did not produce a result (exit $exitCode). Logs: $LogDir" }
    $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($exitCode -ne 0 -or $result.schema -ne 1 -or $result.mode -cne $Mode -or $result.ok -isnot [bool] -or -not $result.ok) {
        throw "Python check/repair failed (exit $exitCode). Logs: $LogDir"
    }
    if ($Mode -eq 'repair' -and ($result.provision_ok -isnot [bool] -or -not $result.provision_ok -or $result.installed_tag -cne 'v0.3.8')) {
        throw "Engine verification result is incomplete. Logs: $LogDir"
    }
    return $result
}

function Invoke-RepairMain {
    [CmdletBinding()]
    param([switch]$CheckOnly, [string]$CrewInstallDir, [string]$CrewHome, [string]$RestoreLongPathsFrom)
    $ErrorActionPreference = 'Stop'
    Set-StrictMode -Version 2.0
    $logDir = $null
    try {
        if (-not (Test-IsWindows)) { throw 'This workaround supports Windows only.' }
        $logDir = New-WorkaroundLogDirectory
        Write-Host "Logs: $logDir"
        if ($RestoreLongPathsFrom) {
            $backup = Read-LongPathsBackup $RestoreLongPathsFrom
            if ($CheckOnly) { Write-Host 'Backup is valid for this computer. CheckOnly: no registry changes.'; return 0 }
            if (-not (Test-IsAdministrator)) { throw 'Restore requires Run as administrator.' }
            $before = Get-LongPathsState
            $before | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $logDir 'before-restore.json') -Encoding UTF8
            Set-LongPathsState $backup.original
            $after = Get-LongPathsState
            if ($after.exists -ne $backup.original.exists -or $after.kind -cne $backup.original.kind -or $after.value -ne $backup.original.value) {
                throw 'Registry restore read-back did not match the backup.'
            }
            Write-Host 'Original long-path registry value restored. No app files were rolled back. Restart affected apps; Windows may require a restart.'
            return 0
        }
        if (-not $CrewInstallDir) { $CrewInstallDir = Join-Path $env:LOCALAPPDATA 'Programs\KiroCrew' }
        if (-not $CrewHome) {
            if ($env:KIROCREW_HOME) { $CrewHome = $env:KIROCREW_HOME }
            else { $CrewHome = Join-Path $env:USERPROFILE '.kiro\crew' }
        }
        foreach ($path in @($CrewInstallDir, $CrewHome)) {
            if ([string]::IsNullOrWhiteSpace($path) -or -not [IO.Path]::IsPathRooted($path) -or $path.StartsWith('\\') -or
                -not (Test-Path -LiteralPath $path -PathType Container)) {
                throw 'Install directory and Crew home must be existing absolute local directories. Use the account that installed Crew.'
            }
        }
        $CrewInstallDir = [IO.Path]::GetFullPath($CrewInstallDir).TrimEnd('\')
        $CrewHome = [IO.Path]::GetFullPath($CrewHome).TrimEnd('\')
        if ($CrewHome -eq [IO.Path]::GetPathRoot($CrewHome).TrimEnd('\') -or
            $CrewHome -eq $CrewInstallDir -or
            $CrewHome.StartsWith($CrewInstallDir + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $CrewHome.StartsWith($env:WINDIR.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Crew home cannot be a drive root, an installation directory or a Windows system directory.'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $CrewHome 'config.json') -PathType Leaf)) {
            throw 'Existing Crew home/config.json is required. No configuration will be created or replaced.'
        }
        $python = Join-Path $CrewInstallDir 'resources\backend-dist\kirocrew-backend\python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Bundled Python not found: $python" }
        $running = @(Get-RunningCrewProcesses $CrewInstallDir)
        $running | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $logDir 'running-crew.json') -Encoding UTF8
        if ($running.Count -gt 0) {
            Write-Host ('Crew-related processes: ' + (($running | ForEach-Object { '{0} (PID {1})' -f $_.Name,$_.Id }) -join ', '))
            if (-not $CheckOnly) { throw 'Exit Kiro Crew, including its tray/background gateway, then run the workaround again. No processes were stopped.' }
        }
        $probe = Invoke-BundledPython -Python $python -CrewHome $CrewHome -Mode check -LogDir $logDir
        $original = Get-LongPathsState
        Assert-LongPathsState $original
        Write-Host ('LongPathsEnabled: ' + ($original | ConvertTo-Json -Compress))
        if ($CheckOnly) { Write-Host 'CheckOnly passed. No provisioning or registry changes were performed.'; return 0 }
        if (-not (Test-IsAdministrator)) { throw 'Right-click Run-Workaround.cmd and choose Run as administrator.' }
        # Recheck immediately before writes; never kill a process automatically.
        if (@(Get-RunningCrewProcesses $CrewInstallDir).Count -gt 0) { throw 'Crew started during preflight. Exit it and retry.' }
        $backup = [ordered]@{
            schema='kirocrew-pptx-longpaths-backup/v1'; computer=$env:COMPUTERNAME
            createdUtc=[DateTime]::UtcNow.ToString('o')
            registryPath='HKLM\SYSTEM\CurrentControlSet\Control\FileSystem'; valueName='LongPathsEnabled'
            original=$original
        }
        $backupFile = Join-Path $logDir 'long-paths-before.json'
        $backup | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $backupFile -Encoding UTF8
        Set-LongPathsState ([pscustomobject]@{ exists=$true; kind='DWord'; value=1 })
        $enabled = Get-LongPathsState
        if (-not $enabled.exists -or $enabled.kind -cne 'DWord' -or $enabled.value -ne 1) { throw 'LongPathsEnabled read-back failed.' }
        Write-Host "Machine-wide long paths enabled. Original registry backup: $backupFile"
        Write-Host 'Provisioning with the bundled Python in a new process. This may take several minutes.'
        $result = Invoke-BundledPython -Python $python -CrewHome $CrewHome -Mode repair -LogDir $logDir
        Write-Host 'PPTX engine provision and interpreter/import checks passed. Start Crew normally from the Start menu.' -ForegroundColor Green
        Write-Host 'Long paths remain enabled. Future Crew/engine updates may require an upstream fix or a revised workaround.'
        return 0
    } catch {
        Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red
        if ($logDir) {
            ($_ | Out-String) | Set-Content -LiteralPath (Join-Path $logDir 'failure.log') -Encoding UTF8
            Write-Host "Logs retained: $logDir"
        }
        return 1
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-RepairMain @PSBoundParameters)
}

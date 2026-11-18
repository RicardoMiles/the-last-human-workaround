#requires -Version 5.1
<#
Contract/failure tests with no Pester dependency. Run in a fresh PowerShell process:
  pwsh -NoProfile -File ./Test-Workaround.ps1
All platform, elevation, process, registry and Python boundaries are mocked.
Only disposable fixtures/logs under the system temporary directory are written.
These tests do not run Crew, provision an engine, or access the real registry.
Real child-process capture and Windows integration require separate validation.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
. (Join-Path $PSScriptRoot 'Repair-PptxMaker.ps1')

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -cne $Expected) { throw "$Message (expected '$Expected', got '$Actual')" }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Pattern)
    $thrown = $null
    try { & $Action | Out-Null } catch { $thrown = $_.Exception.Message }
    Assert-True ($null -ne $thrown) 'Expected an exception.'
    Assert-True ($thrown -match $Pattern) "Unexpected exception: $thrown"
}

function Copy-TestState {
    param($State)
    return [pscustomobject]@{ exists=$State.exists; kind=$State.kind; value=$State.value }
}

# Install every system boundary mock before invoking any product function.
function Test-IsWindows { return $script:FakeWindows }
function Test-IsAdministrator { return $script:FakeAdministrator }
function Start-Process {
    $script:UnexpectedProcessCalls++
    throw 'A direct process start bypassed the mocked Python boundary.'
}
function Stop-Process {
    $script:UnexpectedProcessCalls++
    throw 'The workaround must not stop a process.'
}
function Get-RunningCrewProcesses {
    param([string]$InstallDir)
    $script:ProcessChecks++
    if ($script:ActiveCrew -or ($script:StartCrewDuringPreflight -and $script:ProcessChecks -gt 1)) {
        return [pscustomobject]@{ Id=1234; Name='KiroCrew.exe'; ExecutablePath=(Join-Path $InstallDir 'KiroCrew.exe') }
    }
}
function Get-LongPathsState {
    $script:RegistryReads++
    return (Copy-TestState $script:RegistryState)
}
function Set-LongPathsState {
    param($State)
    Assert-LongPathsState $State
    $script:RegistryWrites++
    $script:WrittenStates += (Copy-TestState $State)
    if (-not $script:FailRegistryReadback) { $script:RegistryState = Copy-TestState $State }
}
function New-WorkaroundLogDirectory {
    $script:LastLogDir = Join-Path $script:FixtureRoot ('logs-' + [guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $script:LastLogDir)
    return $script:LastLogDir
}
function Invoke-BundledPython {
    param([string]$Python, [string]$CrewHome, [string]$Mode, [string]$LogDir)
    $script:PythonModes += $Mode
    Assert-Equal $Python $script:FixturePython 'Unexpected executable passed to Python boundary.'
    Assert-Equal $CrewHome $script:FixtureHome 'Unexpected Crew home passed to Python boundary.'
    if ($Mode -eq 'check' -and $script:CheckFailure) { throw $script:CheckFailure }
    if ($Mode -eq 'repair' -and $script:RepairFailure) { throw $script:RepairFailure }
    if ($Mode -eq 'repair' -and $script:PythonWarning) {
        $script:PythonWarning | Set-Content -LiteralPath (Join-Path $LogDir 'repair-stderr.log') -Encoding UTF8
        Write-Host $script:PythonWarning
    }
    # This boundary returns an already validated result or throws, like the real helper.
    return [pscustomobject]@{
        schema=1; mode=$Mode; ok=$true; crew_version='0.12.2'; python_version='3.12.16'
        engine_tag='v0.3.8'; engine_commit='b7c7fcf0972b33a480f1b717a3de5bc288d53742'
        provision_ok=$true; installed_tag='v0.3.8'
    }
}

function Reset-TestScenario {
    $script:FakeWindows = $true
    $script:FakeAdministrator = $true
    $script:ActiveCrew = $false
    $script:StartCrewDuringPreflight = $false
    $script:FailRegistryReadback = $false
    $script:CheckFailure = ''
    $script:RepairFailure = ''
    $script:PythonWarning = ''
    $script:RegistryState = [pscustomobject]@{ exists=$true; kind='DWord'; value=0 }
    $script:RegistryReads = 0
    $script:RegistryWrites = 0
    $script:WrittenStates = @()
    $script:ProcessChecks = 0
    $script:UnexpectedProcessCalls = 0
    $script:PythonModes = @()
    $script:LastLogDir = $null
}

function Invoke-TestMain {
    param([hashtable]$Extra=@{})
    $arguments = @{ CrewInstallDir=$script:FixtureInstall; CrewHome=$script:FixtureHome }
    foreach ($name in $Extra.Keys) { $arguments[$name] = $Extra[$name] }
    $script:LastConsole = Join-Path $script:FixtureRoot ('console-' + [guid]::NewGuid().ToString('N') + '.log')
    $values = @(Invoke-RepairMain @arguments 6> $script:LastConsole)
    Assert-Equal $values.Count 1 'Main must return one exit status.'
    Assert-True ($values[0] -is [int]) 'Main exit status must be an integer.'
    return $values[0]
}

function Assert-NoRepairWrites {
    Assert-Equal $script:RegistryWrites 0 'Preflight failure changed the registry.'
    Assert-True (-not ($script:PythonModes -contains 'repair')) 'Preflight unexpectedly provisioned an engine.'
    Assert-Equal $script:UnexpectedProcessCalls 0 'Unexpected direct process manipulation.'
}

function New-TestBackup {
    param($State=$script:RegistryState, [hashtable]$Changes=@{})
    $backup = [ordered]@{
        schema='kirocrew-pptx-longpaths-backup/v1'; computer=$env:COMPUTERNAME
        createdUtc='2026-11-18T00:00:00Z'
        registryPath='HKLM\SYSTEM\CurrentControlSet\Control\FileSystem'
        valueName='LongPathsEnabled'; original=$State
    }
    foreach ($name in $Changes.Keys) { $backup[$name] = $Changes[$name] }
    $path = Join-Path $script:FixtureRoot ('backup-' + [guid]::NewGuid().ToString('N') + '.json')
    $backup | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

function Invoke-ContractTest {
    param([string]$Name, [scriptblock]$Body)
    Reset-TestScenario
    try {
        & $Body
        $script:Passed++
        Write-Host "PASS $Name"
    } catch {
        $script:Failures += "${Name}: $($_.Exception.Message)"
        Write-Host "FAIL ${Name}: $($_.Exception.Message)" -ForegroundColor Red
    }
}

$script:FixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('kirocrew-pptx-contract-' + [guid]::NewGuid().ToString('N'))
$script:FixtureInstall = Join-Path $script:FixtureRoot 'Crew-install'
$script:FixtureHome = Join-Path $script:FixtureRoot 'Crew-home'
$script:FixturePython = Join-Path $script:FixtureInstall 'resources\backend-dist\kirocrew-backend\python.exe'
$savedEnvironment = @{}
foreach ($name in @('WINDIR','LOCALAPPDATA','USERPROFILE','COMPUTERNAME','UV_PYTHON','KIROCREW_HOME')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$script:Passed = 0
$script:Failures = @()
try {
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $script:FixturePython) -Force)
    [void](New-Item -ItemType Directory -Path $script:FixtureHome -Force)
    '{}' | Set-Content -LiteralPath (Join-Path $script:FixtureHome 'config.json') -Encoding UTF8
    '' | Set-Content -LiteralPath $script:FixturePython -Encoding UTF8
    $env:WINDIR = Join-Path $script:FixtureRoot 'Windows'
    $env:LOCALAPPDATA = $script:FixtureRoot
    $env:USERPROFILE = $script:FixtureRoot
    $env:COMPUTERNAME = 'CONTRACT-TEST-PC'
    $env:UV_PYTHON = 'contract-test-parent-sentinel'
    $env:KIROCREW_HOME = $script:FixtureHome

    Invoke-ContractTest 'CheckOnly permits nonadmin, validates, and never provisions or writes registry' {
        $script:FakeAdministrator = $false
        Assert-Equal (Invoke-TestMain @{ CheckOnly=$true }) 0 'CheckOnly failed.'
        Assert-Equal ($script:PythonModes -join ',') 'check' 'CheckOnly did not perform exactly one preflight.'
        Assert-NoRepairWrites
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $script:LastLogDir 'long-paths-before.json'))) 'CheckOnly created a repair backup.'
    }
    Invoke-ContractTest 'Unsupported Crew version fails before registry writes or provisioning' {
        $script:CheckFailure = 'Supported kirocrew version is 0.12.2; found 0.13.0'
        Assert-Equal (Invoke-TestMain) 1 'Unsupported Crew version reported success.'
        Assert-NoRepairWrites
    }
    Invoke-ContractTest 'Unsupported bundled interpreter fails before registry writes or provisioning' {
        $script:CheckFailure = 'Supported bundled interpreter is CPython 3.12.16 x64; found 3.15.2'
        Assert-Equal (Invoke-TestMain) 1 'Unsupported Python reported success.'
        Assert-NoRepairWrites
    }
    Invoke-ContractTest 'Unsupported engine pin fails before registry writes or provisioning' {
        $script:CheckFailure = 'The installed PPTX engine pin is outside this workaround scope.'
        Assert-Equal (Invoke-TestMain) 1 'Unsupported engine pin reported success.'
        Assert-NoRepairWrites
    }
    Invoke-ContractTest 'Nonadmin repair refuses before any registry write' {
        $script:FakeAdministrator = $false
        Assert-Equal (Invoke-TestMain) 1 'Nonadmin repair reported success.'
        Assert-NoRepairWrites
        Assert-True ((Get-Content -LiteralPath (Join-Path $script:LastLogDir 'failure.log') -Raw) -match 'administrator') 'Nonadmin failure did not explain elevation.'
    }
    Invoke-ContractTest 'Active Crew refuses repair without stopping processes or provisioning' {
        $script:ActiveCrew = $true
        Assert-Equal (Invoke-TestMain) 1 'Active Crew repair reported success.'
        Assert-NoRepairWrites
        Assert-Equal $script:PythonModes.Count 0 'An active Crew instance reached Python preflight.'
    }
    Invoke-ContractTest 'Crew starting during preflight is caught before registry writes' {
        $script:StartCrewDuringPreflight = $true
        Assert-Equal (Invoke-TestMain) 1 'Process race reported success.'
        Assert-NoRepairWrites
        Assert-Equal $script:ProcessChecks 2 'Missing immediate prewrite process recheck.'
    }
    Invoke-ContractTest 'Unexpected registry type fails safely before replacement' {
        $script:RegistryState = [pscustomobject]@{ exists=$true; kind='String'; value='0' }
        Assert-Equal (Invoke-TestMain) 1 'Unexpected registry type reported success.'
        Assert-NoRepairWrites
    }
    Invoke-ContractTest 'Registry read-back mismatch prevents provisioning and keeps original backup' {
        $script:FailRegistryReadback = $true
        Assert-Equal (Invoke-TestMain) 1 'Read-back mismatch reported success.'
        Assert-True (-not ($script:PythonModes -contains 'repair')) 'Read-back failure still provisioned.'
        $backup = Read-LongPathsBackup (Join-Path $script:LastLogDir 'long-paths-before.json')
        Assert-Equal $backup.original.value 0 'Backup lost the original value.'
    }
    Invoke-ContractTest 'Backend nonzero failure stays failure and retains logs plus original backup' {
        $script:RepairFailure = 'Python check/repair failed (exit 7).'
        Assert-Equal (Invoke-TestMain) 1 'Backend failure became a success status.'
        Assert-Equal ($script:PythonModes -join ',') 'check,repair' 'Repair was not attempted after preflight.'
        Assert-True ((Get-Content -LiteralPath (Join-Path $script:LastLogDir 'failure.log') -Raw) -match 'exit 7') 'Backend exit detail was lost.'
        Assert-True ((Get-Content -LiteralPath $script:LastConsole -Raw) -notmatch 'checks passed') 'Failure printed the success message.'
        Assert-Equal (Read-LongPathsBackup (Join-Path $script:LastLogDir 'long-paths-before.json')).original.value 0 'Failure lost the original registry backup.'
    }
    Invoke-ContractTest 'Validated successful backend result remains successful despite warning output' {
        $script:PythonWarning = 'Warning: optional capability unavailable; provisioning succeeded.'
        Assert-Equal (Invoke-TestMain) 0 'Warning output was treated as failure.'
        Assert-Equal $script:RegistryState.value 1 'Repair did not enable long paths.'
        Assert-True (Test-Path -LiteralPath (Join-Path $script:LastLogDir 'repair-stderr.log')) 'Warning log was not retained.'
        Assert-Equal $env:UV_PYTHON 'contract-test-parent-sentinel' 'Repair changed the parent interpreter environment.'
        Assert-Equal $env:KIROCREW_HOME $script:FixtureHome 'Repair changed the parent Crew home environment.'
    }
    Invoke-ContractTest 'Restore faithfully restores an absent registry value without provisioning' {
        $absent = [pscustomobject]@{ exists=$false; kind=$null; value=$null }
        $path = New-TestBackup -State $absent
        Assert-Equal (Invoke-TestMain @{ RestoreLongPathsFrom=$path }) 0 'Absent-value restore failed.'
        Assert-Equal $script:RegistryWrites 1 'Restore did not perform exactly one write.'
        Assert-Equal $script:RegistryState.exists $false 'Restore created a previously absent value.'
        Assert-Equal $script:PythonModes.Count 0 'Restore attempted engine provisioning.'
    }
    Invoke-ContractTest 'Restore preserves an existing DWORD value' {
        $path = New-TestBackup -State ([pscustomobject]@{ exists=$true; kind='DWord'; value=0 })
        $script:RegistryState.value = 1
        Assert-Equal (Invoke-TestMain @{ RestoreLongPathsFrom=$path }) 0 'DWORD restore failed.'
        Assert-Equal $script:RegistryState.value 0 'DWORD value was not restored.'
    }
    Invoke-ContractTest 'Restore CheckOnly validates backup without registry access or provisioning' {
        $path = New-TestBackup
        $script:FakeAdministrator = $false
        Assert-Equal (Invoke-TestMain @{ RestoreLongPathsFrom=$path; CheckOnly=$true }) 0 'Restore preflight failed.'
        Assert-NoRepairWrites
        Assert-Equal $script:RegistryReads 0 'Restore CheckOnly read the live registry.'
        Assert-Equal $script:PythonModes.Count 0 'Restore CheckOnly launched Python.'
    }
    Invoke-ContractTest 'Foreign-computer backup is rejected without registry changes' {
        $path = New-TestBackup -Changes @{ computer='ANOTHER-PC' }
        Assert-Equal (Invoke-TestMain @{ RestoreLongPathsFrom=$path }) 1 'Foreign backup was accepted.'
        Assert-NoRepairWrites
        Assert-Equal $script:RegistryReads 0 'Foreign backup touched the live registry.'
    }
    Invoke-ContractTest 'Backup schema and registry target cannot redirect restore' {
        foreach ($change in @(@{ schema='other/v1' }, @{ registryPath='HKLM\SOFTWARE\Other' }, @{ valueName='OtherValue' })) {
            $path = New-TestBackup -Changes $change
            Assert-Throws { Read-LongPathsBackup $path } 'schema, computer or registry target'
        }
        Assert-NoRepairWrites
    }
    Invoke-ContractTest 'Malformed registry backup values are rejected' {
        foreach ($state in @(
            [pscustomobject]@{ exists='false'; kind=$null; value=$null },
            [pscustomobject]@{ exists=$false; kind='DWord'; value=0 },
            [pscustomobject]@{ exists=$true; kind='DWord'; value='1' },
            [pscustomobject]@{ exists=$true; kind='QWord'; value=1 },
            [pscustomobject]@{ exists=$true; kind='DWord'; value=[long]2147483648 }
        )) {
            $path = New-TestBackup -State $state
            Assert-Throws { Read-LongPathsBackup $path } 'Invalid|absent|DWORD|range'
        }
        Assert-NoRepairWrites
    }
    Invoke-ContractTest 'Nonadmin restore refuses before writing' {
        $script:FakeAdministrator = $false
        $path = New-TestBackup
        Assert-Equal (Invoke-TestMain @{ RestoreLongPathsFrom=$path }) 1 'Nonadmin restore succeeded.'
        Assert-NoRepairWrites
    }
    Invoke-ContractTest 'Non-Windows fails before any system boundary or Python call' {
        $script:FakeWindows = $false
        Assert-Equal (Invoke-TestMain) 1 'Non-Windows invocation succeeded.'
        Assert-NoRepairWrites
        Assert-Equal $script:RegistryReads 0 'Non-Windows invocation touched registry.'
        Assert-Equal $script:PythonModes.Count 0 'Non-Windows invocation launched Python.'
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    if (Test-Path -LiteralPath $script:FixtureRoot) { Remove-Item -LiteralPath $script:FixtureRoot -Recurse -Force }
}

Write-Host ("{0} passed; {1} failed. Registry, processes and provisioning were mocked." -f $script:Passed,$script:Failures.Count)
if ($script:Failures.Count -gt 0) {
    foreach ($failure in $script:Failures) { Write-Host $failure -ForegroundColor Red }
    exit 1
}
exit 0

# Windows PPTX Maker installation workaround

One-time workaround for the installation failures reported in [KiroCrew #38862](https://github.com/kirodotdev/KiroCrew/issues/38862) and [#38863](https://github.com/kirodotdev/KiroCrew/issues/38863). It prints and retains errors that the UI can hide ([#38864](https://github.com/kirodotdev/KiroCrew/issues/38864)); it does not patch the UI or audit code.

**Supported scope:** Windows x64, installed `kirocrew` **0.12.2**, bundled CPython **3.12.16**, PPTX engine **v0.3.8** pinned to `b7c7fcf0972b33a480f1b717a3de5bc288d53742`. Preflight rejects other versions before changing the registry. This is an internal backend interface workaround, so reassess it after updates.

**Validation (2026-11-18):** selecting bundled Python and calling the installed backend's complete `provision()` succeeded in the recorded Windows T2/T4 comparisons. This wrapper passed actual `-CheckOnly` and syntax checks on Windows PowerShell **5.1.26100.9818**, with `LongPathsEnabled` unchanged at `0`. Its 19 mocked contract/failure tests passed on both that Windows host and macOS PowerShell 7.6.6; three separate macOS child-process tests checked large stdout/stderr capture, nonzero exits and false success results. The newly packaged scripts have **not yet completed an end-to-end repair on Windows**.

## Windows 上怎么运行

1. 下载此目录中的两个文件到同一个本地文件夹，保留 `Repair-PptxMaker.ps1` 和 `Run-Workaround.cmd` 文件名；也可以下载仓库 ZIP，解压后进入 `src/kirocrew/windows-pptx-maker/`。
2. 使用安装 Kiro Crew 的同一个 Windows 账号。先保存工作并彻底退出 Crew，包括托盘和后台 gateway。
3. **右键 `Run-Workaround.cmd` →“以管理员身份运行”**，接受 Windows UAC 提示。不要换成另一个管理员账号。
4. 等待结果。成功会显示 `PPTX engine provision and interpreter/import checks passed`；随后从开始菜单正常打开 Crew。失败时保留窗口显示的日志目录。

管理员权限用于修改系统级长路径设置。此管理员入口下，Crew 自己的安装流程也会以该账号的提升权限运行，在当前账号的 Crew/SDPM 数据目录写入引擎、依赖和相关配置。请先阅读下面的修改范围。

## What the script does

- Checks the installed bundle, exact supported versions, engine pin, existing Crew `config.json`, and resolved app data paths. It reports running Crew processes and refuses an actual repair while they are running; it never stops them itself.
- Backs up the original `LongPathsEnabled` existence, registry type and value, with the computer name, to a new log directory. It sets `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` to DWORD `1` and reads it back. **This is a persistent, machine-wide Windows change**, not a Crew preference. Existing processes can cache the old setting; the repair uses a new Python process. Restart other affected apps; Windows may also require a restart.
- Sets `UV_PYTHON` to Crew's bundled interpreter **only in its child process**. It does not persist environment variables or install a different Python. It uses the installed backend's `provision()` and the supplied existing Crew home, retaining the product's normal sandbox and governance policies.
- Lets that product flow download the pinned engine, build its dependencies, stage app resources, render agent configuration, seed an absent deck-output setting and scan templates. Existing product configuration is not replaced with the empty configuration used in the historical isolated tests. These are real writes to the selected Crew home and the user's SDPM configuration/template metadata.
- Verifies the returned outcome, pinned installed engine tag, interpreter file, and an actual engine-interpreter `sdpm` import. Standard error is logged separately: a product warning on stderr alone is not treated as an installation failure.

No engine directory is deleted by this wrapper, and it does not edit the installed source, sandbox settings, machine execution policy, proxy, VPN or TLS settings. The installed product controls its own staging and replacement. A successful install does not prove every PowerPoint generation or preview feature works.

## Check without repairing

From PowerShell in the downloaded directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Repair-PptxMaker.ps1 -CheckOnly
```

This can run without administrator rights and while Crew is open. It imports the installed backend to inspect paths and compatibility, reads registry state and writes diagnostic logs, but does not call `provision()` or modify the registry. Python is run with `-I -B` to avoid current-directory/Python-environment imports and bytecode writes. `-ExecutionPolicy Bypass` applies to this PowerShell process only; organization policy can still prevent execution.

The default install directory is `%LOCALAPPDATA%\Programs\KiroCrew`. The default Crew home is an existing `KIROCREW_HOME` environment override, otherwise `%USERPROFILE%\.kiro\crew`. For a supported custom installation or existing data directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Repair-PptxMaker.ps1 `
  -CheckOnly -CrewInstallDir 'D:\Apps\KiroCrew' -CrewHome 'D:\CrewData'
```

After checking, run the same command without `-CheckOnly` in an administrator PowerShell window. Use the same Windows account. Paths must be existing absolute local directories; drive roots, the installation directory and Windows system directories are rejected as Crew homes. The script requires the selected home's existing `config.json` and never creates a substitute. Do not point it at an isolated reproduction home expecting it to repair your normal instance.

## Logs and a failed repair

Every invocation gets a new directory under `%LOCALAPPDATA%\TheLastHumanWorkaround\pptx-maker\`. It contains compatibility/provision results and separate complete stdout/stderr logs; a repair also has `long-paths-before.json`. Logs can contain local paths and product diagnostics; review them before publishing.

A failed repair leaves its logs and the backed-up registry state available. It does not automatically toggle a machine-wide setting back while another process may be using it. Product sandbox/governance refusals, an unavailable network resource or a different dependency failure are reported rather than bypassed. Keep those errors for diagnosis.

## Restore only the long-path registry setting

From an administrator PowerShell window, use the backup path printed by the specific repair run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Repair-PptxMaker.ps1 `
  -RestoreLongPathsFrom 'C:\Users\YOUR_NAME\AppData\Local\TheLastHumanWorkaround\pptx-maker\RUN_DIRECTORY\long-paths-before.json'
```

The backup must have this script's schema, fixed registry target and matching computer name. Only an originally absent value or a DWORD value is accepted; other registry types cause a stop before any registry change. Values are validated before writing. If the original value did not exist, restoration removes only `LongPathsEnabled`; otherwise its original type and value are restored and read back. Adding `-CheckOnly` validates the backup without changing anything. Each repair makes its own backup; choose the one whose original state you intend to restore.

This **only restores that registry value**. It does not uninstall the engine, revert dependencies or app/SDPM configuration, or constitute a full rollback. Restart affected applications after changing the value. Reverting it can make a future installation/build fail again.

## Developer checks

Run the isolated contract/failure tests from a fresh PowerShell process:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Test-Workaround.ps1
```

On macOS, use `pwsh -NoProfile -File ./Test-Workaround.ps1`. These tests mock registry, process and provisioning boundaries; they write only disposable temporary fixtures. They do not install an engine or replace the actual Windows preflight.

## Windows references

- [Microsoft: maximum path length limitation](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation) explains the machine-wide registry value, application opt-in and process caching.
- [Microsoft: Start-Process and Windows file verbs](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process?view=powershell-5.1) documents the `RunAs` verb for `.cmd` files.
- [Microsoft: execution policy scopes](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies) explains process scope and Group Policy precedence.

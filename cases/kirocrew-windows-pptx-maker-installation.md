# KiroCrew: Windows PPTX Maker installation stuck in a retry loop

A colleague asked me for help because PPTX Maker would not install on their Windows laptop.
They had already updated Crew and related components. I connected to the computer and tried
with the VPN both enabled and disabled. The installation still failed, and the frontend kept
offering another retry without explaining what needed to change.

Giving the error to the local Crew agent, including in a fresh session, did not identify the
underlying causes in this investigation. The advice returned to retrying the frontend.
I worked through the installation stages and got the engine installed on the colleague's laptop.
After that, I reproduced the failures on my own Windows desktop and successfully installed
the engine there too. I separated the findings into three issues.

## One incident, three findings

| Finding | Report | What the evidence established |
| --- | --- | --- |
| Incompatible Python selection | [KiroCrew #38862](https://github.com/kirodotdev/KiroCrew/issues/38862) | Dependency installation selected managed Python 3.15.2, but `pywin32==311` had no cp315 wheel. Crew's bundled Python 3.12.16 was available. |
| Deep Windows build paths | [KiroCrew #38863](https://github.com/kirodotdev/KiroCrew/issues/38863) | With the compatible interpreter selected, building `sdpm-skill` could still fail beneath the temporary `.engine-fetch` directory. |
| Unhelpful failure presentation | [KiroCrew #38864](https://github.com/kirodotdev/KiroCrew/issues/38864) | The engine API retained the useful error, while the UI showed a generic failure and the matching failed audit event had an empty error field. |

The initial experience looked like a resource that would never download, despite the package
existing. The later captured Python failure occurred after the download had completed.
Download failure, dependency resolution failure, and package build failure must stay distinct.

## Status and environment

| Component | Recorded value |
| --- | --- |
| Crew | Desktop app 0.12.2, Stable, `win32-x64` |
| Colleague's laptop | Windows 11 Enterprise, build 26200 |
| My Windows desktop | Windows 11, build 26200, x64 |
| Bundled Python | CPython 3.12.16 |
| Bundled uv | 0.13.7, revision `316c827e0` |
| PPTX engine | v0.3.8, commit `b7c7fcf0972b33a480f1b717a3de5bc288d53742` |

I did not establish that the two Windows editions or cumulative-update revisions matched.
Managed Python 3.15.2 was deliberately made available before the reproduction; I have not
proved that Crew automatically downloaded that interpreter on the original machine.

## Controlled comparisons

On my Windows desktop, the tests called the installed PPTX backend's complete `provision()`
rather than only building an individual package. The two comparisons isolated different causes:

| Run | Interpreter / path setting | Result |
| --- | --- | --- |
| T1 | `UV_PYTHON` unset; managed 3.15.2 selected; long paths enabled | Failed at `pywin32==311`, which had no cp315 wheel. |
| T2 | `UV_PYTHON` selected bundled 3.12.16; long paths enabled | Completed with `engine ready at v0.3.8`. |
| T3 | Bundled 3.12.16; long paths disabled | Failed while building `sdpm-skill`, with `WinError 206`. |
| T4 | Bundled 3.12.16; long paths enabled | Completed with `engine ready at v0.3.8`. |

T1 and T2 used fresh, equally short data directories and shared a test uv cache.
T3 and T4 used fresh homes at the same absolute path, separate empty uv caches, and new Python
processes. The successful T4 staging path was one character longer because its PID was longer.
The long-path setting was restored after that comparison.

On the original laptop, selecting bundled Python first exposed a second failure:
`Errno 2` on `dependency_links.txt` during the nested build. Enabling Windows long paths then
allowed installation to finish, but I did not preserve that successful run's log.
The second machine supplied the recorded failed/successful pair; its error was `WinError 206`
on the egg-info directory, not the original laptop's exact error.

## What the frontend hid

The frontend reported `the new engine failed to build; keeping the existing one`.
An authenticated `GET /api/apps/pptx-maker/engine` exposed the Python/pywin32 cause, while the
matching `pptx_maker.engine_provision` failed audit event still contained `error=""`.
That API error tail was held in memory; it was not a persistent diagnostic log.

I reproduced the button-driven Python failure and UI/API/audit discrepancy through the web UI
of an isolated Windows gateway with its own Crew home. This was not a GUI reproduction of the
long-path failure. The original laptop's audit summary counted 27 started and 27 failed
attempts with empty errors, including earlier download failures, not 27 identical Python errors.

These were actionable installation failures. Repeating the same button could not change the
selected interpreter or the Windows path setting. The product needs to expose the failed
stage and its cause, retain a useful failure record, and choose a compatible build environment.

## Windows workaround

The accompanying [workaround instructions](../src/kirocrew/windows-pptx-maker/README.md),
[PowerShell helper](../src/kirocrew/windows-pptx-maker/Repair-PptxMaker.ps1), and
[Windows entry point](../src/kirocrew/windows-pptx-maker/Run-Workaround.cmd) package the two
environment changes for this recorded release. Read the instructions before running them.

1. Close Crew. **Right-click `Run-Workaround.cmd` and select Run as administrator** on the
   affected Windows computer, using the same Windows account that owns its Crew data.
2. The helper checks Crew 0.12.2, bundled Python 3.12.16, and the pinned v0.3.8 engine commit.
   A different release needs a fresh compatibility check rather than an assumed match.
3. It saves the previous machine-wide `LongPathsEnabled` state before setting it to `1`.
   It starts a new bundled-Python process, supplies `UV_PYTHON` temporarily, and calls the
   product's own `provision()` implementation.
4. Keep its saved state and diagnostic output. Success requires exit code zero, a successful
   provisioning outcome, and the installed-engine/import checks. Stderr warnings alone do not
   indicate failure; both output streams are retained.

From the helper directory, a preflight without installation or registry changes is available as:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Repair-PptxMaker.ps1 -CheckOnly
```

`-CheckOnly` does not provision the engine or modify the registry. To restore the recorded
long-path setting, run the following from an elevated terminal, replacing the state-file path:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Repair-PptxMaker.ps1 -RestoreLongPathsFrom 'C:\path\to\saved-state.json'
```

Restoring that setting does not uninstall the engine. The helper does not permanently set
`UV_PYTHON`, change sandbox settings, delete an engine, or repair Crew's UI and audit behavior.
It does not resolve unrelated network/download failures or establish that PPTX generation works.

## Evidence boundaries

- The historical Windows tests validate the interpreter and long-path mechanisms. The new
  helper passed Windows PowerShell 5.1 syntax and actual `-CheckOnly` validation on the
  Windows desktop; `LongPathsEnabled` stayed at `0`. Its complete repair has not been
  rerun on Windows. All 19 mocked contract/failure tests passed on Windows PowerShell 5.1 and
  macOS PowerShell 7.6.6. Three separate macOS process-capture tests covered large output,
  nonzero exits and false success results. These checks do not replace a complete repair run.
- The original laptop's scratch-copy `uv sync` success is not a captured successful GUI install.
- The long-path tests isolated Crew engine directories, but not all upstream `%APPDATA%\sdpm`
  template metadata; no before/after snapshot exists for that metadata.
- A calculated 267-character candidate path is not the object directly named in T3's error.
  Redacted usernames also change displayed lengths; 260 is not a universal operation threshold.
- `engine ready` confirms provisioning, not a successfully generated or rendered presentation.
- The normal desktop diagnostic bundle remains private because it includes unrelated history.
  The issue reports contain the relevant reproduction excerpts.

## Current handling

As checked on 2026-11-18, all three issues remain open. [#38862 has an acknowledged claim
through 2026-11-24](https://github.com/kirodotdev/KiroCrew/issues/38862#issuecomment-6969073357).
My [PPTX Maker PR #10](https://github.com/KiroCrewCaptain/kirocrew-app-pptx-maker/pull/10)
is open for review (its text calls it a draft; GitHub does not mark it as a draft PR).
It proposes addressing #38863 by shortening
the staged source path by 84 characters. Its Windows base/head comparison kept long paths
disabled: the base failed with `WinError 206`, while the proposed version passed.

That integration test used a host-spawn test adapter and a SHA256-checked cached archive;
it did not test the gateway, UI, production downloader, or OS sandbox. Takeover of #38863
has not been confirmed, and the change has not been released or adopted into Crew core.
The workaround above remains for the installed 0.12.2 release; PR #10 is not a released fix.

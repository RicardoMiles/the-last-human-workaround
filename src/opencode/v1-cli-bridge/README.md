# OpenCode v1 CLI bridge

This helper runs an independently pinned OpenCode 1.19.4 shell CLI as a local, password-protected server so an OpenCode v1 desktop client can connect to it through the normal external-server flow. It is a compatibility workaround for a documented product split: the v1 desktop client no longer supports free models while the v1 shell CLI may still do so.

It is intentionally narrow: loopback only, no header rewriting, no copied credentials or session databases, and no promise that a future server policy will continue to accept free models. The public helper is separate from any already-running local service.

## Requirements

- macOS arm64 or x86_64 with Python 3.9+ and the standard library (the source-host recovery was tested on arm64)
- Network access to the official GitHub release metadata for every install, plus a release ZIP or `--download` (a local `--source` skips the asset download, not metadata verification)
- A v1 desktop client that can add an external server
- Permission to run the pinned CLI against the project locations you intend to use

The installer verifies the release before it can run: GitHub release asset SHA-256, Apple signing Team ID `5NZ4Q7NXJ4`, Mach-O format, and reported version `1.19.4`. A failed check stops installation; the helper does not modify the system CLI or desktop application.

## Install and operate

From the repository root:

```bash
BRIDGE=src/opencode/v1-cli-bridge/opencode_v1_bridge.py

# Inspect the current helper-owned installation and service state.
python3 "$BRIDGE" inspect

# Verify and install a local copy from a downloaded release asset.
python3 "$BRIDGE" install --download --port 4096

# Or verify a release ZIP that you already downloaded.
python3 "$BRIDGE" install --source /path/to/OpenCode-1.19.4.zip --port 4096

# Start, inspect, or stop only the helper-owned service.
python3 "$BRIDGE" start
python3 "$BRIDGE" status
python3 "$BRIDGE" stop

# Run in the foreground while diagnosing startup or permissions.
python3 "$BRIDGE" foreground

# Print a connection description with the secret redacted by default.
python3 "$BRIDGE" connection
python3 "$BRIDGE" connection --show-secret

# Stop and remove the helper-owned service and generated files.
python3 "$BRIDGE" rollback
```

`install` does not start the service. `start` checks the binary digest, requested port, and `/global/health`. A start that cannot verify health exits with status 1 and explicitly reports that its own job remains loaded. After handling a permission prompt, retry `status` or `start`; use `stop` to unload it. It never kills a port's unrelated owner. Use `foreground` for diagnosis. `--show-secret` requires a TTY; do not copy its output into tickets, shell history, or chat.

In the desktop app, open Settings → Servers → Add server, enter the loopback URL and the generated password, make the server the default, and open the project through that server. Reopen a prior session through the new server when its history is needed. A desktop tab bound to the old sidecar does not migrate automatically.

## Verification matrix

| Check | What it establishes | What it does not establish |
| --- | --- | --- |
| Release SHA-256, Team ID, Mach-O, version | The selected executable matches the expected public v1 release | That the provider will preserve free-model access |
| `status` health response | The local process is reachable and reports its version | That a model request will succeed |
| Desktop server connection | The GUI can use the loopback backend | That old tabs or session stores were migrated |
| A completed model request | End-to-end behavior for that account, project, and model at that time | Permanent compatibility or a general provider guarantee |

Treat these as separate gates: a healthy HTTP server is not an available model, and a successful request does not restore v1 desktop support.

## Security and scope

The server uses a newly generated BasicAuth password. The helper stores its password and service metadata in a private support directory; neither is committed here. The bind address is `127.0.0.1`. Do not expose the port or change the bind address without reviewing authentication and network exposure.

That local password authenticates GUI → CLI only; it is not a provider API key or a free-tier entitlement. At runtime OpenCode still uses its normal user configuration and provider access. None of those credentials are shipped here. Signature checks must run successfully in your normal local user context; a restrictive automation sandbox can prevent validation. Do not disable the checks or use sudo as a generic fix.

The helper uses an isolated binary copy and support name, `OpenCodeV1Bridge`, with launch label `local.opencode.v1-bridge`. It does not stop or overwrite another service, change the GUI's saved server, revoke TCC grants, or edit authentication data. `rollback` only handles files and service state created by this helper; remove the GUI connection and TCC grant manually.

The support tree is published as one directory. If execution is interrupted before the separate LaunchAgent file is published, rerunning the same install can repair that file after validating the complete owned tree. This tests process-interruption recovery, not filesystem durability through power loss. A different port requires rollback and reinstall; it does not silently rewrite a running configuration. The launch environment includes Homebrew and `~/.local/bin` but does not source interactive profiles. Extra provider or MCP environment requirements still need explicit configuration.

## Failure table

| Symptom | First check | Safe response |
| --- | --- | --- |
| Verification rejects the asset | Re-download from the official release and run `inspect` | Do not bypass the checksum, Team ID, Mach-O, or version checks |
| Port is occupied | `status` and a separate listener check | Choose another local port and add that exact URL in the GUI |
| Health is 200 but inference fails | Confirm model/account capability and inspect the server version | Record it as an end-to-end failure; do not “fix” it by rewriting headers |
| LaunchAgent starts but requests time out | Run `foreground`, inspect macOS privacy prompts, and check the pinned binary directly | Grant only the required app access; do not grant Python Full Disk Access as a generic workaround |
| GUI still shows the old error card | Reopen the same session through the new server | Treat historical cards as history, not a current health signal |

The helper is a cleaned-up, independently testable local recovery setup. Cross-machine portability, reboot behavior, and rollback of arbitrary third-party installations are not claimed.

## Tests and measured limits

```sh
python3 -m unittest discover -s src/opencode/v1-cli-bridge -p 'test_*.py' -v
```

The 29 tests use temporary directories and mocked commands/network calls, including interruption recovery and foreign-service rejection. A separate arm64 macOS check verified the real official ZIP and exercised isolated installation/reinstall/rollback with real `codesign`, `file`, and `--version` checks; launchd calls were mocked. The source-host GUI inference is recorded in the [case](../../../cases/opencode-v1-free-model-bridge.md). The public helper has not replaced that host's working service. CI runs the isolated suite on Linux (Python 3.9/3.13) and macOS (Python 3.13).

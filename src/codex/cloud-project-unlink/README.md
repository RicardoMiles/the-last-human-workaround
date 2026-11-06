# Workaround for Codex Local Workspace Unlink with Unexpected Cloud Project Persistance

An offline helper for one manually confirmed, unintended ChatGPT cloud-project / Codex local-project association. It removes the saved association while preserving the local project, its folders, and its chats. See the [case record](../../../cases/codex-cloud-project-local-error-persistance-workaround.md) for the original incident and the difference between the successful one-off repair and this reusable helper.

This is a version-limited workaround for private storage formats. It repairs an existing association; it does not fix the creation trigger or prevent the app from creating another association later. A linked project is not automatically a broken project. Normal local work can also have cloud backing.

## Requirements and supported profile

- macOS, Python 3.9+, and the standard library; no packages need installing.
- Desktop bundle `com.openai.codex`, version `26.1029.61003`, build `24614`, with the reviewed `state_5.sqlite` schema. Unknown versions or schemas cannot write. Do not change the version constants alone to bypass this gate after an upgrade.
- Exactly one consistent mapping across the local project's `chatGptBacking`, database metadata `codex.chatGptBacking`, and a provisioning journal in state `backed`. Conflicting, duplicate, incomplete, recovering, or retired mappings are refused.
- Normal shutdown of the desktop and Codex executors before any write. The helper does not terminate processes. Its process check is deliberately conservative and may also block unrelated Codex CLIs.

Defaults are `~/.codex` and `/Applications/ChatGPT.app`. Supply `--codex-home` and `--app-bundle` before the subcommand for another location. A saved plan fixes its source home and application bundle; write and verification commands use those saved locations.

Unlinking removes the local item's backing relationship and associated cloud-chat/settings entry points. It does not migrate cloud chats into local chats. A project whose cloud association is wanted is not a candidate for this tool.

## Inspect and plan

From the repository root:

```bash
UNLINK=src/codex/cloud-project-unlink/codex_project_unlink.py
python3 "$UNLINK" inspect
python3 "$UNLINK" plan --local-project-id LOCAL_PROJECT_ID --out /tmp/codex-unlink-plan.json
```

Select the exact local ID from `inspect` after confirming that the association is unintended. Do not select by a shared display name. `plan` writes a private preview, not live project state, and refuses output inside Codex home or the application bundle. The output file must not already exist.

Plans and backups contain real local paths and pairing/account identifiers. Keep them outside this repository and do not attach them to a public issue. Inspection can also print real project names and IDs. Only the source code, synthetic fixtures, and sanitized documentation are included here.

## Apply after shutdown

Save other work and quit the desktop and Codex CLI/app-server processes normally. Run the following from a separate Terminal, keeping the app closed until it finishes:

```bash
python3 "$UNLINK" apply --plan /tmp/codex-unlink-plan.json --backup-dir ~/.codex/backups/project-unlink-example --wait-seconds 120
```

The backup directory must be new, its parent must exist, and it must share a filesystem with the provisioning journal. Within Codex home, only the `backups` subtree is allowed; other backup locations must be outside Codex home and the application bundle. A stale plan, changed schema/version, competing mapping, or running executor stops the operation.

The helper removes only the two backing fields and archives the matching provisioning journal. It does not delete projects, folders, or chats and makes no network requests. Its advisory lock coordinates instances of this helper, not arbitrary external editors or the desktop application. Keep the application closed during writes.

After reopening the app, perform a read-only verification:

```bash
python3 "$UNLINK" verify --backup-dir ~/.codex/backups/project-unlink-example
```

The local project remains registered. Removing its sidebar entry is a separate UI operation. The helper does not invoke Remove or infer cloud deletion behavior from the current sidebar mode.

## Rollback and interrupted operations

Quit the application/executors before either command:

```bash
python3 "$UNLINK" rollback --backup-dir ~/.codex/backups/project-unlink-example
python3 "$UNLINK" recover --backup-dir ~/.codex/backups/project-unlink-example
```

`rollback` restores this operation's association. `recover` uses the same target-level restoration for known interrupted states; it is not a request to keep cleaning other projects. Both preserve later chats, other projects, and unrelated metadata. If the target's identity or backing has changed, or a conflicting journal/mapping exists, the helper refuses to overwrite it.

JSON, SQLite, and the separate journal cannot form one atomic transaction. A durable manifest and before/after checks support recovery from the tested mixed states. The full database backup is retained for diagnosis and is never automatically restored over the live database. Unknown external changes require review.

## Tests and references

```bash
python3 -W error::ResourceWarning -B -m unittest discover -s src/codex/cloud-project-unlink -p 'test_*.py' -v
python3 -O -W error::ResourceWarning -B -m unittest discover -s src/codex/cloud-project-unlink -p 'test_*.py'
```

The 18 synthetic-data tests cover unlinking, recovery, conflicts, and preservation of later chats. They passed on Python 3.9.6 and 3.14.8, including `-O`.

Real-data checks covered read-only inspection and the process guard; apply/rollback with this reusable helper remain untested.

- [Original report: openai/codex #58806](https://github.com/openai/codex/issues/58806)
- [OpenAI Docs: sidebar Remove](https://learn.chatgpt.com/docs/reference/troubleshooting)

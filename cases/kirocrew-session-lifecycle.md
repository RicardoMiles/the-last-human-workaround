# KiroCrew: from Older Sessions cleanup to a clearer session lifecycle

I started this investigation just because the messy conversations and auto-folding pissed me off. I could no longer tell which conversations I wanted to keep and which I wanted to permanently delete. 
* Sessions I have manually closed were in the old sessions
* Auto-folded conversations that I might need to archive into project were in the old sessions

I tried configuration changes, adopted a folder workflow, and wrote a cleanup script. The upstream review later helped me identify the version boundary I had missed. But I still find the current Dormant and Older Sessions design frustrating. For moderate to heavy users like me, figuring out where conversations went feels like needless housekeeping. It interrupts our work with agents and makes the workspace harder to keep tidy.

This case connects my original bug report, [#10857](https://github.com/kirodotdev/KiroCrew/issues/10857), with my subsequent UX proposal, [#18235](https://github.com/kirodotdev/KiroCrew/issues/18235). It records both the useful workaround and the assumptions I corrected along the way.

## Status and environment

| Upstream item | Current handling as of 2026-10-08 | Local verification |
| --- | --- | --- |
| [#10857: Older Sessions should exclude active / pinned / foldered sessions](https://github.com/kirodotdev/KiroCrew/issues/10857) | Closed on 2026-09-24 as not reproducible. I supplied the missing version context in a [follow-up comment](https://github.com/kirodotdev/KiroCrew/issues/10857#issuecomment-6069044796) on 2026-10-08. | I could no longer reproduce the original open-session overlap on v0.7.2. |
| [#18235: clarify Dormant vs Older lifecycle and keep project sessions together](https://github.com/kirodotdev/KiroCrew/issues/18235) | Open; no comments, assignee, or linked PR at the time of checking. | The remaining concern is the session-management experience, including Dormant collapsing inside curated projects. |

| Environment | Original investigation | Follow-up comparison |
| --- | --- | --- |
| Date | 2026-09-14 | 2026-10-08 |
| Kiro Crew | v0.3.0 | v0.7.2 |
| kiro-cli | 2.21.4 | 2.28.0 |
| Platform | macOS, Apple Silicon (`arm64`) | Same device and platform |
| Dashboard configuration | `restore_sessions=true`, `restore_window_minutes=0` | Same settings |

The comparison above is my manual test, documented in the linked follow-up. It does not establish that every session-management edge case is resolved.

## 1. The original friction: keeping and deleting looked too similar

I had accumulated conversations that needed different treatment: some belonged in a project, some were useful history, and some were disposable. In my original installation, all 36 on-disk sessions appeared in Older Sessions, including sessions still open in the sidebar and sessions I had pinned or filed into folders.

Closing an unwanted conversation did not create a recognizable transition into that list: it was already there. The history rows in that client also did not expose `closed` or `closed_at`. I had to remember my intent outside the UI to distinguish “keep this for later” from “remove this permanently.”

My initial report treated the missing open-slot exclusion as a general defect in the current implementation. The observation described my installation; the generalization was wrong because I had omitted its version.

## 2. The configuration workaround and its limits

I changed the `dashboard` section of `~/.kiro/crew/config.json`:

```json
{
  "dashboard": {
    "restore_sessions": true,
    "restore_window_minutes": 0
  }
}
```

- `restore_sessions: true` allows startup restoration of ordinary sessions rather than restricting that restoration to foldered or pinned ones.
- `restore_window_minutes: 0` removes the last-activity cutoff from that startup restoration path.

In my v0.3.0 installation, this kept non-closed sessions restored and addressed the automatic-retirement problem I was experiencing. It did not fix the Older Sessions overlap. That depended on the frontend requesting `exclude_open=1` and the backend matching the open slots to their transcript keys.

I also had to correct what “restore” meant. Background rehydration and visible tab restoration are separate paths. The latter reads the saved keys in `~/.kiro/crew/open_slots.json`; folder/pin metadata participating in rehydration does not, by itself, guarantee a visible tab. These settings also do not control the current frontend's Dormant collapse.

## 3. Folders helped me express intent, but cleanup still took a manual lookup

I began using project folders for conversations I wanted to keep. In the restoration code I inspected, foldered or pinned sessions bypassed the recency cutoff, making those attributes useful retention signals.

For unwanted conversations, I tried a `ToBeDelete` folder. In the folder view I was using, I could close a conversation but could not permanently delete it there. I then had to find the same conversation in Older Sessions to finish the cleanup. It felt like doing a manual VLOOKUP between two views.

Deleting the folder was another misleading possibility. The inspected `api_chat_folder_delete()` implementation removed the grouping and cleared members' `folder_id`; it did not call `delete_session()`. Deleting my cleanup folder would scatter its conversations rather than remove them.

This led to a more direct workaround: put unwanted conversations in a dedicated folder, resolve that folder to its ID, list its members, and delete those members through KiroCrew's own `ConversationLog` API.

## 4. The folder-deletion script

The original script is preserved unchanged at [`src/kirocrew/delete_folder_sessions.py`](../src/kirocrew/delete_folder_sessions.py). It reads the folder registry, selects sessions by `folder_id` from `list_sessions()`, and calls `delete_session(key)` for each selected member. It does not hand-edit session JSON or implement its own file-removal logic.

The script uses KiroCrew's internal Python APIs, which can change between releases. It needs the bundled interpreter containing `kiro_crew`; the path below is from my macOS Apple Silicon installation.

### Actual command behavior

The script's header describes `--folder` as a dry run, but its implementation prompts for deletion when stdin is a terminal. Answering `y` or `yes` deletes the listed sessions. `--confirm DELETE` and `--yes` skip that prompt. To make the preview step unambiguously non-destructive, redirect stdin from `/dev/null` and omit both deletion flags.

Run these commands separately, reviewing each result before moving on:

```bash
# 1. List folder names, IDs, and member counts.
/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12 \
  "$HOME/Git/the-last-human-workaround/src/kirocrew/delete_folder_sessions.py"

# 2. Preview the selected folder only. Replace TBD with the folder's name.
/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12 \
  "$HOME/Git/the-last-human-workaround/src/kirocrew/delete_folder_sessions.py" \
  --folder TBD < /dev/null
```

The script matches the name case-insensitively, resolves its ID, and refuses to choose if multiple folders share that name. The preview prints titles, last-activity times, and session keys.

Before the deletion stage, shut down KiroCrew and its associated backend/gateway processes completely, then review the preview again. During my exploration, deleting while live slots remained in memory allowed shutdown persistence to write sessions back to disk. Deleting first and quitting afterward was the wrong order.

Only after checking the target list, with those processes stopped:

```bash
# 3. Permanently delete the sessions currently in the selected folder.
/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12 \
  "$HOME/Git/the-last-human-workaround/src/kirocrew/delete_folder_sessions.py" \
  --folder TBD --confirm DELETE
```

`delete_session()` removes session data and its associated sidecars; the script provides no undo or backup. It re-reads membership on each invocation, does not stop processes itself, and does not bind deletion to a saved preview. Check its per-session results before reopening the app. This repository preserves the workaround; adding this case did not execute it against my sessions.

## 5. What the review changed

The [upstream reproduction](https://github.com/kirodotdev/KiroCrew/issues/10857#issuecomment-5675233586) kept pinned and foldered live sessions out of Older Sessions, including after a restart. It also identified a likely version boundary: the exclusion had shipped in v0.4.0 on 2026-08-27. My September report was still about v0.3.0.

The issue was [closed as not reproducible](https://github.com/kirodotdev/KiroCrew/issues/10857#issuecomment-5818023578) after I did not provide the requested version and reproduction details in time. On 2026-10-08, I returned to the original device, recovered the environment snapshot, and compared it with v0.7.2 / kiro-cli 2.28.0.

That explained the mismatch: the old frontend did not send the later `exclude_open=1` request. The current installed frontend and backend support that path, and my current manual test correctly excluded open sessions. In my [follow-up](https://github.com/kirodotdev/KiroCrew/issues/10857#issuecomment-6069044796), I thanked the reviewers, acknowledged the missing version information, and supplied the corrected account.

The lesson for my future reports is concrete: include the exact installed versions and request path before presenting a local source read as an explanation of current upstream behavior.

## 6. The remaining product question

The current filtering works in my test. I still find the relationship between Dormant, Older Sessions, folders, pins, close, and delete difficult to reason about. In the current behavior described in [#18235](https://github.com/kirodotdev/KiroCrew/issues/18235), Dormant means an open session visually collapsed by recency; Older means history outside the current open-slot set.

As a heavy user, moving loose conversations into a project is deliberate curation. I expect expanding that project to show its complete context. Applying Dormant collapsing inside it hides part of a collection I have already organized, splitting the project by activity time rather than by my intent.

My proposal is:

1. Give project/folder membership precedence over global Dormant collapsing by default.
2. Make collapsing inside a project an explicit per-project option.
3. Use a clearer label, such as `Inactive open sessions`, and explain open-slot and restart behavior beside the relevant groups.
4. Provide an explicit Archive action and explain the lifecycle `Open → Inactive/Collapsed → Archived → Deleted`.
5. Treat Project/Folder and Pin as organization and retention attributes. Inactive/Collapsed should describe visibility without silently implying archival or deletion.

The trade-off is a longer project list. A per-project collapse option lets users choose compactness while preserving the default expectation that an expanded project shows its contents. These are proposed behaviors, not claims about features already implemented.


## 7. Source navigation from the local investigation

These line numbers are retained from my local investigation notes, relative to `kiro_crew/`. They are snapshot offsets rather than stable references to current `main`; no immutable source commit is attached to this table. In particular, the `api_sessions()` row must be read with the v0.3.0 request-path limitation explained above.

| Method / endpoint | Relative path | Recorded line(s) |
| --- | --- | --- |
| `ConversationLog.list_sessions()` | `history.py` | Definition: 2840; `key=path.stem`: 2861; `folder_id`: 2879 / 2897 |
| `ConversationLog.delete_session()` | `history.py` | 3487 |
| `ConversationLog.clear_closed()` | `history.py` | 3673 |
| `ConversationLog.get_metadata()` | `history.py` | 4081 |
| `api_sessions()` — `GET /api/sessions` | `dashboard/handlers/sessions.py` | Definition: 944; inventory read: 969; historical request lacked effective open-slot exclusion |
| `_restore_recent_sessions_steps()` | `dashboard/chat_persistence.py` | Definition: 831; `closed` veto: 869 |
| `restore_open_slots()` | `dashboard/chat_persistence.py` | 337 |
| `_restore_open_slots_steps()` — reads `open_slots.json` | `dashboard/chat_persistence.py` | 239 |
| `save_all_slots_to_history()` | `dashboard/chat_persistence.py` | 188 |
| `DashboardState._persist_open_slots()` | `dashboard/state.py` | 3821 |
| `api_chat_folder_delete()` — ungroups members | `dashboard/chat_folders.py` | 485 |

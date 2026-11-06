# Codex: Workaround for unexpected local companion to a cloud project

Firstly, based on my daily user experience, there's a dropdown menu at the top left of the ChatGPT desktop app's sidebar that lets users switch between Codex and ChatGPT. Given that, I suppose the codex sessions and ChatGPT sessions would be different. And actually I use ChatGPT and Codex for different work. I barely use any chat session on the desktop application. Some day, a project I had only used in ChatGPT cloud (web page) also appeared under the Codex category, with an empty local folder and a saved link to the cloud project. I had not asked to create a local workspace or run a local task for it. Updates and full restarts did not remove the entry.

So, I checked and updated my understanding of how different codex sessions work. I double checked OpenAI docs [learn.chatgpt.com/docs/projects?surface=app](https://learn.chatgpt.com/docs/projects?surface=app) that explain the definition of chat/work sessions on the web, and local codex sessions' correlation with work sessions.

The main friction here is I can't get my Codex workspace organized by keeping the accidentally emerged project. The same display name did not tell me whether an action would target the local registration, the cloud project, or both. I am afraid that removing the local shadow would unexpectedly delete my cloud chat project as the link has been built by ChatGPT Desktop. I reported the case in [openai/codex #58806](https://github.com/openai/codex/issues/58806), and implemented a workaround on my local machine later on.

## Status and environment

The [original issue&#39;s environment section](https://github.com/openai/codex/issues/58806#environment) records a check on 2026-11-02. I checked the hardware, OS, installed desktop metadata, and CLI versions again on 2026-11-05:

| Item                      | Original report: 2026-11-02                  | Current machine: 2026-11-05                  |
| ------------------------- | -------------------------------------------- | -------------------------------------------- |
| Hardware                  | MacBook Pro, model `Mac15,6`, Apple M3 Pro | MacBook Pro, model `Mac15,6`, Apple M3 Pro |
| Architecture              | `arm64`                                    | `arm64`                                    |
| macOS                     | `26.6.2`, build `25G83`                  | `26.6.2`, build `25G83`                  |
| Desktop app               | `26.1027.51404`, build `23880`           | `26.1029.61003`, build `24614`           |
| Desktop bundle identifier | `com.openai.codex`                         | `com.openai.codex`                         |
| Bundled CLI               | Not recorded in the issue                    | `codex-cli 0.166.0-alpha.6`                |
| Separate terminal CLI     | Not recorded in the issue                    | `codex-cli 0.164.0`                        |

The same issue identifies desktop process `26.1016.62342` at the original event on 2026-10-27 and `26.1023.50603` after a restart that evening. Those are historical desktop versions, separate from the November measurements and from both CLI versions. The current desktop and bundled CLI versions were read from the installed application; the terminal CLI version came from the command on PATH.

## Why it is worth a wheel-building

As a heavily reliant user, project management is  a part of my daily AI Workflow; scattering temporary sessions and projects not aligned with my naming rules will lead to messy management. Codex is basically my workspace; if any of the projects there is from ChatGPT Web Chat, it will definitely confuse me. And its link to the cloud project makes the delete operation risky as well. So an essential workaround should be logged there.

## What the saved state established

Read-only inspection found three matching records: `chatGptBacking` in `.codex-global-state.json`, `codex.chatGptBacking` in the SQLite project's metadata, and a `project-chatgpt-provisioning` journal in state `backed`.

The original investigation found an empty local directory and zero matching local conversations, including archived records. The saved local association was real; this was more than a duplicated label. That finding did not show that the cloud conversations had been migrated into local sessions.

The local records were created on 2026-10-27, shortly after I switched between old cloud conversations. A new cloud conversation was created almost four hours later. The exact click and selected mode were not logged, so I could not establish the initial trigger or produce a clean-profile reproduction. A connection heartbeat was not evidence of a local task submission.

## Keep the local project and remove its backing

By the time I repaired the association on 2026-11-05, the local project also contained chats. I kept the project rather than deleting its registration. The one-off repair removed the two backing fields and moved the matching provisioning journal into a private backup. Keeping the journal in its original location would leave a recovery record referring to the old association.

I backed up the state and tested the patch and handled-failure rollback on copies first. A detached script waited for normal desktop and app-server shutdown before writing, so the running application's cache could not immediately restore the old association. The completion record confirmed all three changes at 19:27 UTC. Later read-only checks still found the local project and folder, with both backing fields and the original journal absent. The script sent no cloud requests.

That recovered the intended separation on this machine. It did not identify or fix whatever originally created the association.

## Where this workaround applies

I built v0.1.0 for one manually confirmed, unwanted local/cloud association that has finished provisioning. Finding `chatGptBacking` alone does not identify a bug: a project deliberately used for local work can have the same kind of association. I select the exact local project ID and require all three saved records to agree before planning a change.

| Requirement          | v0.1.0 write boundary                                                                                                                                                                                                                                 |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Platform and runtime | macOS with Python 3.9+ and the standard library                                                                                                                                                                                                       |
| Desktop app          | Bundle `com.openai.codex`, version `26.1029.61003`, build `24614`                                                                                                                                                                               |
| Storage              | `state_5.sqlite` with the reviewed fingerprint of `user_version` and the `projects`/`threads` columns, foreign keys, and triggers; the fingerprint is pinned in the [helper source](../src/codex/cloud-project-unlink/codex_project_unlink.py) |
| Target association   | Exactly one matching global-state record, database project mapping, and provisioning journal in state `backed`; no creation/recovery/retirement state or conflicting mapping                                                                        |
| Execution            | The desktop and Codex executors must be closed normally and remain closed during writes; the helper checks this and sends no cloud requests                                                                                                           |

My currently installed desktop and storage profile match this write boundary. The older versions in the environment record are evidence of the incident's history, not additional supported helper versions. After an application or schema change, compatibility needs another review before enabling writes.

The code does not require Apple Silicon or check CPU architecture, and it does not pin a particular macOS release. My `arm64` Mac is the measured setup; Intel Macs and other macOS releases have not been validated. Windows and Linux writes are outside this implementation.

The operation keeps the local project, folders, chats, and unrelated metadata. It removes the local item's backing relationship and its associated cloud-chat/settings entry points. It does not repair stale cwd or project membership, migrate cloud chats, or fix the trigger that created the association.

## Related repairs, without assuming the same bug

[#56325](https://github.com/openai/codex/issues/56325) also required app-server changes, legacy-state reconciliation, and a restart. Its user had deliberately started local work, then moved a chat and encountered stale cwd/roots and a lingering registration. Its recovery removed the old local registration. My case began without intended local work, and my repair retained the project. The shared need to coordinate saved state is useful evidence; a shared root cause has not been established.

The [cockpit-tools repair script](https://github.com/jlcodes99/cockpit-tools/blob/cb10069dfd56b988b1ed7a2b8edfe8568b6e6d07/scripts/repair_codex_local_projects.py#L94-L145) has audit/apply/backup/rollback operations but preserves cloud-associated projects. [#58630](https://github.com/openai/codex/issues/58630) independently requests a backing-unlink option. Neither supplied the same three-part unlink tool in the 2026-11-05 check.

At that check, my original issue remained open with no comments. I would like a supported way to inspect this relationship and remove just the local association, with a clear account of what remains. Until then this helper is a manual, version-limited recovery option. It changes the local item's cloud-associated entry points, and it cannot prevent the creation trigger from running again.

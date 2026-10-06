# OpenCode: resuming sessions by title from the shell

I spent an afternoon trying to resume OpenCode sessions by title from the shell. I built a `opencode session list → jq → fzf → oepncode -s <sessionID>` pipeline, then found that [#48718](https://github.com/anomalyco/opencode/issues/48718) had proposed essentially the same solution. This note records the friction I encountered, how I arrived at the workaround, and what I felt inconsistently low-efficiency the resume flows gap from Codex and Claude Code.

## The friction

When I return to an older conversation, I remember what I was working on, not its random sessionID. In OpenCode, `-s` resumes by sessionID, while the title-based session list is available after entering the TUI with `/sessions`. I wanted to choose a session from my shell and land in that conversation directly.

There are two parts to that experience: a picker or list I can launch before the main TUI, and a way to find the right sessionID by its title. Resuming the latest session with `-c` does not solve either part when the one I want is older.

## How I got to the workaround

OpenCode already exposes the two pieces I needed: `opencode session list --format json` provides IDs and titles, and `opencode -s <id>` opens a chosen session. I used `jq` to turn the JSON into a two-column list, `fzf` to search the titles, and `cut` to pass the selected ID back to OpenCode.

```bash
session_id="$(
  opencode session list --format json |
    jq -r '.[] | [.id, (.title // "Untitled")] | @tsv' |
    fzf |
    cut -f1
)"

if [ -n "$session_id" ]; then
  opencode -s "$session_id"
fi
```

The command still passes an ID to OpenCode, but I can search by title instead of looking up that ID myself. Choosing happens before the OpenCode TUI opens. Canceling the picker leaves `session_id` empty, so the TUI does not start.

This is a shell workaround, not an OpenCode feature. It needs `jq` and `fzf`, searches titles rather than conversation content, and still uses the native `-s` ID argument for the final step.

## What felt different across the three CLIs

The first two rows are the difference that sent me down this path. The other rows provide context from the same comparison; they are not additional feature requests.

| Workflow                                     | Codex CLI                                                 | Claude Code                                                    | OpenCode CLI                                                                       |
| -------------------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Start a session picker from the shell        | `codex resume`                                          | `claude -r` / `claude --resume`                            | No native equivalent in the checked version                                        |
| Prefilter the picker from the shell          | No query argument documented                              | `claude -r bigsur`                                           | No native equivalent                                                               |
| Continue the latest session without a picker | `codex resume --last`                                   | `claude -c`                                                  | `opencode -c`                                                                    |
| Resume using a name rather than an ID        | `codex resume <name>`                                   | `claude -r <name>`; a search term can also narrow the picker | `-s` accepts a session ID                                                        |
| Select a session after entering the TUI      | No in-session command in this comparison                  | `/resume` (alias `/continue`)                              | `/sessions` (aliases `/resume`, `/continue`)                                 |
| Name a session                               | `/rename <name>`                                        | `-n <name>` or `/rename`                                   | `Ctrl+R` in the TUI                                                              |
| Fork a session                               | `codex fork` opens a picker by default                  | `--fork-session` with resume or continue                     | `--fork` with `-s` or `-c`                                                   |
| Scope of “latest”                          | Current working directory by default;`--all` expands it | Current directory                                              | Not strictly current-directory isolated in reported same-repository worktree cases |
| Other session entry points                   | `codex queue --thread <session> --message <text>`       | `--from-pr <number>`                                         | `opencode run -s <id> "message"`                                                 |

The table describes the CLI surface available on 2026-10-05. It does not claim every row was exercised end to end. I checked local command help for Codex 0.156.1, Claude Code 2.1.144, and OpenCode 1.18.34, alongside the linked documentation. I did not publish session data or a terminal recording for this note.

The “latest” row needs a qualification: calling OpenCode `-c` simply “global” would be too broad. [#41562](https://github.com/anomalyco/opencode/issues/41562) reports it selecting a session from another worktree of the same repository. That is a different issue from the title-based picker I was trying to build.

## What I found upstream

After deriving the pipeline, I found [OpenCode #48718](https://github.com/anomalyco/opencode/issues/48718). It includes nearly the same `session list --format json → jq → fzf → -s` workaround. The linked [PR #50052](https://github.com/anomalyco/opencode/pull/50052) proposes making `opencode -s` without an ID open the TUI's `/sessions` list. An earlier request to accept a title with `-s`, [#12404](https://github.com/anomalyco/opencode/issues/12404), was closed as a duplicate.

I am not claiming the command or the feature request is new. I am keeping this note because it shows how I got from a frustrating workflow to a command I could use, and how finding the existing issue changed my conclusion.

## References

- [Codex CLI commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [OpenCode TUI](https://opencode.ai/docs/tui/)
- [OpenCode issue #48718](https://github.com/anomalyco/opencode/issues/48718) and [PR #50052](https://github.com/anomalyco/opencode/pull/50052)
- [OpenCode issue #12404](https://github.com/anomalyco/opencode/issues/12404)

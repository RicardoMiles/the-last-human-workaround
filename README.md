# the-last-human-workaround

Grow with agents before I fade, grow with agents at dusk. While AI grows...

Notes on their rough edges, the workarounds that help, and what we learn along the way.

Real user friction -> Reproducible evidence -> Pratical workarounds -> Better Product Decisions.

I am an IT Engineer working on digital and AI transformation. My work spans employee support workflow automation, software delivery, DevOps, and the responsible adoption of AI tools. AI Agent product is heavily used in my daily life and real workflow. The repository is my public record of my personal exploration. Investigating the gaps I encounter, sharing useful findings upstream, sharing workarounds for my peers are the main work here.

It is an independent portforlio, not an official support channel for any product.

## Scope

I currently follow Kiro Crew, Kiro CLI/IDE, Codex, Claude Code, and OpenCode. I also document adjacent tools when they affect these workflows. A product appearing here does not imply that I have reported an issue for it yet.

Each case aims to show:

1. The affected user workflow and observed behavior.
2. Reproduction steps, versions, and the limits of the available evidence.
3. A workaround with its cost and validation status, if one exists.
4. A product hypothesis: who benefits, what should change, and what trade-offs matter.
5. The upstream conversation and a separate local verification result.

## Field notes and upstream progress

| Case | Tool | Related issue | What it demonstrates | Current handling |
| --- | --- | --- | --- | --- |
| [KiroCrew: dormant & old sessions mechanism design](cases/kirocrew-session-lifecycle.md) | Kiro Crew | [kirodotdev/KiroCrew#18235](https://github.com/kirodotdev/KiroCrew/issues/18235) | Deep-user feedback on project organization, Dormant collapsing, and session lifecycle | Open; no comments, assignee, or linked PR as of 2026-10-08 |
| KiroCrew: ACP sends an unavailable Auto model on Windows | Kiro Crew | [kirodotdev/KiroCrew#17519](https://github.com/kirodotdev/KiroCrew/issues/17519) | A version-specific ACP startup failure with account model-catalogue evidence and a fallback-path hypothesis | Open; triaged and assigned to a maintainer; possible related [PR #15598](https://github.com/kirodotdev/KiroCrew/pull/15598) is open but has merge conflicts |
| Codex: remote session cannot be moved between SSH projects | Codex Desktop | [openai/codex#51394](https://github.com/openai/codex/issues/51394) | A reproducible cross-project session-management failure with a clear fallback expectation | Open; no comments, assignee, or linked PR as of 2026-10-08 |
| Codex: missing middle turns after reopening a chat | Codex Desktop | [openai/codex#42025](https://github.com/openai/codex/issues/42025) | An independent reproduction with intact rollout data but incomplete projected history | Open; no PR linked on the issue |
| OpenCode: session history lost after a provider stream failure | OpenCode Desktop | [anomalyco/opencode#46402](https://github.com/anomalyco/opencode/issues/46402) | Incident forensics, evidence boundaries, and a data-loss prevention proposal | Open; assigned; no PR linked on the issue |
| [OpenCode: resume sessions by title from the shell](cases/opencode-shell-session-resume.md) | OpenCode CLI | [Selector #48718](https://github.com/anomalyco/opencode/issues/48718), [title lookup #12404](https://github.com/anomalyco/opencode/issues/12404) | A shell workaround, a CLI comparison, and the matching upstream proposal | Selector issue open; [V1 PR #48719](https://github.com/anomalyco/opencode/pull/48719) and [V2 PR #50052](https://github.com/anomalyco/opencode/pull/50052) open. Title lookup issue closed as duplicate; no native title resume in OpenCode 1.18.34 |
| CC Switch: per-model built-in tool controls for Codex | CC Switch / Codex | [farion1231/cc-switch#7736](https://github.com/farion1231/cc-switch/issues/7736) | Workaround trade-offs and a product-level capability design | Open; no PR linked on the issue |
| [KiroCrew: old sessions bug](cases/kirocrew-session-lifecycle.md) | Kiro Crew | [kirodotdev/KiroCrew#10857](https://github.com/kirodotdev/KiroCrew/issues/10857) | A corrected hypothesis and a concrete plan for collecting missing evidence | Closed as not reproducible; [missing version context and recheck supplied](https://github.com/kirodotdev/KiroCrew/issues/10857#issuecomment-6069044796) on 2026-10-08. Original overlap observed on v0.3.0; no longer reproduced in the author's v0.7.2 test |

Status checked on 2026-10-08. These are upstream issue and PR states, not confirmation that a fix has shipped or passed a local recheck.

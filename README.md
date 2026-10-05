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

## Field notes

| Case                                                           | Tool              | Related issue                                                                   | What it demonstrates                                                                  | Upstream status*                                       |
| -------------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Codex: missing middle turns after reopening a chat             | Codex Desktop     | [openai/codex#42025](https://github.com/openai/codex/issues/42025)               | An independent reproduction with intact rollout data but incomplete projected history | Open                                                   |
| OpenCode: session history lost after a provider stream failure | OpenCode Desktop  | [anomalyco/opencode#46402](https://github.com/anomalyco/opencode/issues/46402)   | Incident forensics, evidence boundaries, and a data-loss prevention proposal          | Open                                                   |
| CC Switch: per-model built-in tool controls for Codex          | CC Switch / Codex | [farion1231/cc-switch#7736](https://github.com/farion1231/cc-switch/issues/7736) | Workaround trade-offs and a product-level capability design                           | Open                                                   |
| Kiro Crew: Older Sessions investigation                        | Kiro Crew         | [kirodotdev/KiroCrew#10857](https://github.com/kirodotdev/KiroCrew/issues/10857) | A corrected hypothesis and a concrete plan for collecting missing evidence            | Closed as not planned; maintainers could not reproduce |

\*Snapshot on 2026-10-05. A closed issue is **not** proof that a fix shipped.

# Kiro on Big Sur Holdout

My Intel Hackintosh is a Big Sur holdout. It runs macOS 11.7.9 on an i5-8300H, x86_64. I wanted to use KiroCrew without changing the OS, unfortunately official KiroCrew doesn't support my system version. So I tried to settle for second best, installing Kiro CLI `.app` whose metadata lists macOS 10.13 as its minimum. But, even the installed kiro-cli didn't work due to its dependency like buns are quite moden enough to abandon those legacy macOS version.

I have no choice but to get Kiro CLI working with a shim first. At least I don't need to cope with annoying GUI stuff and modern macOS frontend framework.

Although KiroCrew's desktop app was not an option on this machine,  its source repo gave me the idea of getting the Python gateway running then show all the frontend in webbrowser dashboard. As Crew was a bundled react webkit application, there was even no loss of running it in Safari

The next problem showed up in the terminal. Crew started, but local embeddings did not. From my personal view, that was a nightmare to an excellent local AI Agent. So I tried to build on top of the shim I've implemented already with CLI. Under the hood, the compatibiliy issues are the different leaves on the same tree.

## Status and environment

The shims are local workarounds. Upstream reports and verification are tracked below, as of 2026-10-22.

| Upstream item | Status |
| --- | --- |
| [Kiro #6902](https://github.com/kirodotdev/Kiro/issues/6902) | Open. I [posted a macOS loader-failure data point](https://github.com/kirodotdev/Kiro/issues/6902#issuecomment-6328286025), separate from the AVX2 and SIGILL reports. Waiting for feedback on the macOS floor and version preflight. |
| [Kiro #7835](https://github.com/kirodotdev/Kiro/issues/7835) | Still closed with `pending-triage`. The bot removed `duplicate` after my [evidence comment](https://github.com/kirodotdev/Kiro/issues/7835#issuecomment-6328294069). No reopen or maintainer confirmation yet. |
| [Kiro #12062](https://github.com/kirodotdev/Kiro/issues/12062) | Open. I filed the declared macOS 10.13 floor versus Big Sur loader failure as a separate compatibility issue. Waiting for triage on metadata, docs and install or update preflight. |
| Bun Upstream Compatibility | Bun officially requires macOS 13.0 or later, and maintainers have explicitly ended support for older macOS releases. I won't pursue upstream patch to restore legacy macOS compatibility. My work will focus on version specific shims for the agentic tools tracked here. [Bun #13625](https://github.com/oven-sh/bun/issues/13625#issuecomment-2322604989) |
| [Crew PR #21723](https://github.com/kirodotdev/KiroCrew/pull/21723) | Open. I [posted isolated test results](https://github.com/kirodotdev/KiroCrew/pull/21723#issuecomment-6328469906) for head `229b7893`. A real dyld failure returned `model_error_runtime_load`; repaired libraries returned healthy idle in a fresh process. This tested the status handler, not inference or the dashboard UI. |

| Stage | Date | Kiro CLI | Kiro Crew | Local result |
| --- | --- | --- | --- | --- |
| First CLI shim | 2026-09-22 | 2.23.0 | Not part of this step | Got `kiro-cli-chat` running on Big Sur. |
| CLI recheck and Crew embedding fix | 2026-10-21 | 2.33.0 | 0.9.0 | CLI still needed its shim. Crew loaded the patched native libraries. |
| Follow-up check | 2026-10-21 | 2.33.0 | 0.9.0 | Confirmed live shim and model loading. The BLAS forwarder passed a 2x2 matrix check. |

| Environment | Measured setup |
| --- | --- |
| macOS | Big Sur 11.7.9, build 20G1426 |
| CPU | Intel Core i5-8300H |
| Architecture | `x86_64` |
| CLI shim SDK | `MacOSX11.3.sdk` |
| Installed app's declared minimum | macOS 10.13 |
| Chat binary's recorded minimum / SDK | macOS 10.13 / SDK 26.5 |

These are local results for this setup. The remaining limits are listed below.

## CLI first

`kiro-cli-chat` needed symbols from newer libc++, Accelerate and ICU libraries. Big Sur could not provide them. Without the shim, the real binary failed with:

```text
dyld: Symbol not found: __ZTTNSt3__114basic_ofstreamIcNS_11char_traitsIcEEEE
  Expected in: /usr/lib/libc++.1.dylib
```

I built a dylib to fill those gaps and used a wrapper to load it. The wrapper runs a separately signed, non-hardened copy so the `DYLD_*` settings take effect.

This got CLI working. The fix only wraps `kiro-cli-chat`; the other two CLI binaries run normally.

[CLI shim and build steps](../src/kiro-cli/bigsur-missing-symbol-shim/)

## A warning I was not going to ignore

Crew's startup log had several warnings, including old config values and an outdated Node.js version. The important one was:

```text
WARNING kiro_crew.embeddings: Vendored llama-cpp-python failed to import
Symbol not found: _cblas_sgemm$NEWLAPACK$ILP64
```

The gateway could still run, but the local vector search path was unavailable. I need that for semantic memory and retrieval. Losing it was a deal breaker for me as an agent user. It is a big part of the agent experience.

The missing Accelerate symbol looked familiar from the CLI fix. Crew also needed libc++ exception and stream symbols.

I could reuse the approach, but the loading method had to change. Crew's native libraries bind symbols to specific dependencies. Adding another dylib beside them was not enough.

I made two re-export shims, copied the bundled libraries, and pointed the copies at the shims. Crew loads that folder through `LLAMA_CPP_LIB_PATH`.

A symbol scan also missed stream symbols that failed during a real library load. I kept the load test. A clean lookup count was not enough.

[Crew shim and build steps](../src/kirocrew/bigsur-embedding-shim/)

## Keep a working pair

Because of the expirate shim after auto-update of kiro-cli ten days ago, I was so worried about the shim robustness.

I put the Crew override in its launcher and added an idempotent script to restore it after updates. I kept CLI 2.33.0 and Crew 0.9.0 as one known working pair. Till today, they are still the latest stable pair from upstream, so I locked them.

I also locked the patched files against surprise updates. The local maintenance scripts refuse obvious mistakes, such as replacing the real CLI binary with its tiny wrapper.

My upgrade workflow is:

1. Unlock both layers and back up the working copies.
2. Update the chosen component.
3. Restore its shim setup before normal startup.
4. Check both CLI and Crew, then lock them again.

The locking and reapply tools are in place. A full upgrade and backup restore still need a test. The CLI backup folder has not been created yet.

The original scripts depend on my machine's layout. I have added a [portable maintenance helper](../src/kiro-maintenance/) with paths supplied by the reader. It keeps inspection, backup, replacement, re-injection and locking as separate actions.

| Failure | Guard | Recovery | Verification |
| --- | --- | --- | --- |
| A tiny wrapper gets mistaken for the real CLI | Check Mach-O type and a 50 MiB size floor | Reject the candidate and keep the working copy | The real binary and wrapper sizes were checked on my machine. |
| A new copy fails with the current shim | Check the candidate's exit status and version before replacement | Keep the previous copy and inspect the missing symbols | No live upgrade has been tested with the portable helper. |
| A Crew update removes the library override | Reapply only at the expected import anchor; do not duplicate a matching override | Check the new launcher if its layout changed | The original injected launcher is in use. The portable helper is separate. |
| An updater hits locked files | Inspect flags first; make lock and unlock explicit | Unlock deliberately before updating | Original lock flags were checked. Locking is not a backup. |
| The working copy must be recovered | Back up before replacement and keep older backups | Select and verify a known working copy | Backup and restore have not been exercised on the original setup. |

The portable helper passed two isolated safety test sets, with 12 and 16 cases. They cover rejected candidates, retained backups, rollback, launcher injection and lock boundaries. Process results are mocked in these tests; one fixture also checks real macOS immutable-file behavior.

Its read-only check also passed on the Big Sur machine. It recognized the existing launcher override and both patched dependency paths without creating a backup or changing file flags.

The portable helper does not perform updates, sign binaries or restart the gateway. I have not replaced my working machine's scripts with it.

## Where it stands

Daily use feels good. I can use the normal commands without thinking about the fixes each time.

A later check confirmed that live CLI processes load the shim. Crew loads its patched libraries and the local Qwen embedding model. The BLAS forwarder returned the correct result for a 2x2 matrix multiplication. Full embedding vectors have not been compared against a reference.

There are still ICU placeholders and known timezone and plural-range ABI issues. I have not tied them to a failure in my normal workflow. This is a working setup for my use, with those limits still open.

I would like Crew to say what is lost when local embeddings fail. A running gateway does not tell me whether semantic memory works. An OS check before installation would save guesswork too.

There is also a concrete mismatch in my installed CLI 2.33.0. Both its app metadata and chat binary declare macOS 10.13, yet the chat binary fails to load on 11.7.9 without the shim.


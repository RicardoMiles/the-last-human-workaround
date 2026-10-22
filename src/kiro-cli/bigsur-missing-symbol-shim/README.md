# Kiro CLI on Big Sur

My local fix for `kiro-cli-chat` on macOS 11.7.9, Intel x86_64. Tested with CLI 2.33.0.

One dylib supplies missing libc++ stream, Accelerate and ICU symbols. The wrapper loads it with `DYLD_INSERT_LIBRARIES` and `DYLD_FORCE_FLAT_NAMESPACE`.

Only `kiro-cli-chat` needs this. Leave `kiro-cli` and `kiro-cli-term` alone.

## Build and check

Use the Big Sur Command Line Tools and macOS 11.3 SDK. Place your separately prepared, non-hardened `kiro-cli-chat` copy in this directory. The shipped binary's runtime protections can block the injection.

From this directory:

```sh
sh ./build_shim.sh
KIRO_FIX="$PWD" sh ./kiro-cli-chat-wrapper.sh --version
sh ./symcheck.sh "$PWD/kiro-cli-chat"
```

Set `SDK=/path/to/MacOSX11.3.sdk` when building if needed. Check again after each CLI update.

## Limits

Some ICU functions are placeholders. The timezone and plural-range bridges also have known ABI issues. These remain unresolved, although my daily workflow has been working well.

The symbol scan checks flat lookup. The launch check tests startup. Neither proves every runtime path works, and the script can miss some failures.

[Full case](../../../cases/kiro-bigsur-missing-symbols.md)

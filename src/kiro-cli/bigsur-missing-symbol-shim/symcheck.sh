#!/bin/sh
# symcheck.sh - Big Sur shim symbol coverage check.
#
# Usage:
#   ./symcheck.sh /path/to/kiro-cli-chat
#   ./symcheck.sh                 # defaults to ./kiro-cli-chat
#
# When to run: after every Kiro CLI update, after replacing the binary copy,
# and after any shim change.
#
# HOW TO READ THE OUTPUT -- read the "Two judgements" section of README.md
# first. Section 3 measures flat-namespace COVERAGE; only section 4 answers
# "is the shim load-bearing". They are not substitutes for each other.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-$DIR/kiro-cli-chat}"
SHIM="$DIR/kiro_stream_shim.dylib"
BIN="$DIR/.symcheck.bin"
ARCH=x86_64

[ -f "$TARGET" ] || { echo "symcheck: target not found: $TARGET" >&2; exit 1; }
[ -f "$SHIM" ]   || { echo "symcheck: shim not found: $SHIM (run ./build_shim.sh first)" >&2; exit 1; }

# Compile resolver if needed
if [ ! -x "$BIN" ] || [ "$DIR/symcheck.c" -nt "$BIN" ]; then
    /usr/bin/clang -arch "$ARCH" -O1 -o "$BIN" "$DIR/symcheck.c"
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

echo "=== Target ==="
ls -la "$TARGET"
file -b "$TARGET" | head -1
echo

echo "=== 1. undefined symbols ($ARCH) ==="
nm -arch "$ARCH" -u "$TARGET" 2>/dev/null | sed 's/^ *//' | sort -u > "$WORK/undef.txt"
echo "count: $(wc -l < "$WORK/undef.txt" | tr -d ' ')"
echo

echo "=== 2. shim's three fault classes still referenced? ==="
printf 'C++ stream vtable/VTT : %s\n' \
    "$(grep -cE '__ZT[VT].*basic_(ofstream|stringstream|istringstream|ostringstream|filebuf|stringbuf)' "$WORK/undef.txt" || true)"
printf 'cblas ILP64           : %s\n' \
    "$(grep -c 'NEWLAPACK' "$WORK/undef.txt" || true)"
printf 'ICU (static refs)     : %s   <- 0 is expected; ICU is resolved at runtime, nm cannot see it\n' \
    "$(grep -cE '_(ubrk_|ucal_|unumrf_|udtitvfmt_|uplrules_|unumf_)' "$WORK/undef.txt" || true)"
echo

echo "=== 3. flat-namespace resolution (wrapper runtime conditions) ==="
echo "Note: 'MISSING dyld_stub_binder' is a known false positive (dyld internal, invisible to dlsym). Ignore it."
DYLD_FORCE_FLAT_NAMESPACE=1 DYLD_INSERT_LIBRARIES="$SHIM" "$BIN" < "$WORK/undef.txt" 2>/dev/null || true
echo

echo "=== 4. definitive launch test ==="
printf 'without shim : '
if env -u DYLD_INSERT_LIBRARIES -u DYLD_FORCE_FLAT_NAMESPACE "$TARGET" --version 2>&1 \
     | grep -q 'Symbol not found'; then
    echo "launch failed (expected) -- shim is load-bearing"
else
    echo "it launched! -- this build may no longer need the shim, please verify"
fi
printf 'with shim    : '
DYLD_FORCE_FLAT_NAMESPACE=1 DYLD_INSERT_LIBRARIES="$SHIM" "$TARGET" --version 2>/dev/null | head -1 \
    || echo "FAILED -- shim does not cover this build, rebuild or extend shim"

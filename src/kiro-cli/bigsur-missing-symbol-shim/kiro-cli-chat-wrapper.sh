#!/bin/sh
# kiro-cli-chat wrapper: macOS 11 (Big Sur) libc++ missing-symbol bridge.
# The real kiro-cli-chat (built against SDK 26.5) references <fstream>/<sstream>
# VT*/VTT/ctor symbols that only newer libc++ exports; this injects a shim that
# re-emits them from the 11.3 SDK libc++ headers, then runs a non-hardened copy
# of the binary so DYLD_* variables are honoured (see README).
#
# KIRO_FIX must point at the directory holding kiro_stream_shim.dylib and the
# non-hardened kiro-cli-chat copy. Export it, or hardcode it below.
KIRO_FIX="${KIRO_FIX:?set KIRO_FIX to the directory holding kiro_stream_shim.dylib}"
if [ ! -x "$KIRO_FIX/kiro-cli-chat" ]; then
    echo "kiro-cli-chat: bridge files missing at $KIRO_FIX" >&2
    exit 127
fi
export DYLD_FORCE_FLAT_NAMESPACE=1
export DYLD_INSERT_LIBRARIES="$KIRO_FIX/kiro_stream_shim.dylib"
exec "$KIRO_FIX/kiro-cli-chat" "$@"

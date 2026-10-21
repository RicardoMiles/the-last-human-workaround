#!/bin/sh
# Rebuild kiro_stream_shim.dylib from source.
#
# Requires the Command Line Tools compiler and the macOS 11.3 SDK. The SDK
# matters: the whole point is to emit the stream-class instantiations with
# Big Sur's own libc++ ABI, so building against a newer SDK defeats it.
#
#   ./build_shim.sh                                  # default SDK path
#   SDK=/path/to/MacOSX11.3.sdk ./build_shim.sh      # explicit SDK
#
# Output: kiro_stream_shim.dylib next to this script. Inject it with
# kiro-cli-chat-wrapper.sh (DYLD_INSERT_LIBRARIES + DYLD_FORCE_FLAT_NAMESPACE).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
SDK="${SDK:-/Library/Developer/CommandLineTools/SDKs/MacOSX11.3.sdk}"
if [ ! -d "$SDK" ]; then
    echo "SDK not found; set SDK=/path/to/MacOSX11.3.sdk" >&2
    exit 1
fi

# -fvisibility=default is required: the explicitly instantiated stream symbols
# must be EXPORTED for the flat-namespace lookup to find them.
/usr/bin/clang++ -arch x86_64 -isysroot "$SDK" -mmacosx-version-min=11.0 \
    -std=c++17 -fvisibility=default -O2 \
    -dynamiclib -Wl,-install_name,"$DIR/kiro_stream_shim.dylib" \
    "$DIR/kiro_stream_shim.cpp" "$DIR/kiro_cblas_shim.c" "$DIR/kiro_icu_shim.c" \
    -framework Accelerate -framework CoreFoundation \
    -o "$DIR/kiro_stream_shim.dylib"
echo "built $DIR/kiro_stream_shim.dylib"
echo
echo "Verify what it supplies (should be 83 defined symbols):"
echo "  nm -gU -arch x86_64 $DIR/kiro_stream_shim.dylib | wc -l"
echo "Then check coverage against a target binary:"
echo "  ./symcheck.sh /path/to/kiro-cli-chat"

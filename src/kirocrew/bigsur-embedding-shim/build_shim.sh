#!/bin/sh
# Build the two re-export shims and rewire a COPY of KiroCrew's bundled
# llama.cpp libraries to load them, so local embeddings work on macOS 11.
#
#   SRC=<bundled lib dir> DST=<your writable dir> ./build_shim.sh
#
# SRC is the llama_cpp/lib directory inside KiroCrew's virtualenv, e.g.
#   <venv>/lib/python3.12/site-packages/llama_cpp/lib
# DST is a directory you own. The bundled originals are never modified: the
# libraries are COPIED into DST and only the copies are rewired.
#
# Point KiroCrew at the result with LLAMA_CPP_LIB_PATH=<DST>. The upstream
# loader reads that variable via os.environ.setdefault, so setting it also
# skips the CPU-compatibility check.
#
# Why re-export shims rather than plain shims: libllama is bound to
# /usr/lib/libc++.1.dylib through its dependency table (two-level namespace),
# not by flat lookup. The shim must therefore stand IN libc++'s place and
# re-export everything libc++ provides, while adding the few symbols Big Sur
# is missing. This is the key difference from the kiro-cli layer, which runs
# under DYLD_FORCE_FLAT_NAMESPACE and can inject instead.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
: "${SRC:?set SRC to the bundled llama_cpp/lib directory inside the KiroCrew venv}"
: "${DST:?set DST to a writable directory for the patched copies}"
mkdir -p "$DST"

echo "== 1. build the two re-export shims =="
clang++ -arch x86_64 -dynamiclib -std=c++17 \
  -install_name @rpath/libcxx-bigsur-shim.dylib \
  -Wl,-reexport_library,/usr/lib/libc++.1.dylib \
  -o "$DST/libcxx-bigsur-shim.dylib" "$DIR/bad_function_call_shim.cpp"

clang -arch x86_64 -dynamiclib \
  -install_name @rpath/libaccel-bigsur-shim.dylib \
  -Wl,-reexport_library,/System/Library/Frameworks/Accelerate.framework/Versions/A/Accelerate \
  -framework Accelerate \
  -o "$DST/libaccel-bigsur-shim.dylib" "$DIR/cblas_ilp64_shim.c"

echo "== 2. verify the shims define (T/S, not U) what they must =="
# Expect three lines: D1Ev + __ZTI + __ZTV
nm -arch x86_64 "$DST/libcxx-bigsur-shim.dylib" \
  | grep -E "bad_function_callD1Ev|__ZTINSt3__117bad_function_call|__ZTVNSt3__117bad_function_call"
# Expect one line: T _cblas_sgemm$NEWLAPACK$ILP64
nm -arch x86_64 "$DST/libaccel-bigsur-shim.dylib" | grep 'cblas_sgemm.NEWLAPACK.ILP64'

echo "== 3. copy the bundled libraries (originals untouched) =="
cp -p "$SRC"/libggml-base.0.dylib "$SRC"/libggml-blas.0.dylib "$SRC"/libggml-cpu.0.dylib \
      "$SRC"/libggml-metal.0.dylib "$SRC"/libggml.0.dylib "$SRC"/libllama.dylib "$DST"/
# cp -p also copies macOS file flags; clear any uchg so the rewrite below works.
/usr/bin/chflags nouchg "$DST"/*.dylib 2>/dev/null || true

echo "== 4. rewire the dependency tables of the COPIES =="
# The bundled libraries are unsigned, so install_name_tool needs no re-signing.
install_name_tool -change /usr/lib/libc++.1.dylib \
  @rpath/libcxx-bigsur-shim.dylib "$DST/libllama.dylib"
install_name_tool -change /System/Library/Frameworks/Accelerate.framework/Versions/A/Accelerate \
  @rpath/libaccel-bigsur-shim.dylib "$DST/libggml-blas.0.dylib"

echo "== 5. verify the rewiring (read-only) =="
# Expect libcxx-bigsur-shim / libaccel-bigsur-shim, and NO remaining
# /usr/lib/libc++.1.dylib. If libllama has several libc++ references,
# run the -change above once per reference.
otool -L "$DST/libllama.dylib"       | grep -iE 'shim|c\+\+'
otool -L "$DST/libggml-blas.0.dylib" | grep -iE 'shim|accel'

cat <<EOF

Done. Start the gateway with:
  LLAMA_CPP_LIB_PATH="$DST" kirocrew gateway

Then check the startup log contains neither "Symbol not found" nor
"Vendored llama-cpp-python failed to import".

A clean log proves the libraries LOAD. It does not prove the BLAS forwarder
resolved its target -- see the silent-failure note in cblas_ilp64_shim.c.
EOF

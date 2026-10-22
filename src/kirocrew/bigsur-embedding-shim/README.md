# Kiro Crew embeddings on Big Sur

My local fix for Crew 0.9.0 on macOS 11.7.9, Intel x86_64.

The gateway could start, but its bundled llama.cpp libraries failed to load. That disabled local embeddings.

These shims supply missing libc++ and Accelerate symbols. The build script copies the bundled libraries and changes the copies' dependencies. The originals stay untouched.

## Build and use

Build on Big Sur with the Command Line Tools. From this directory, set the source library folder and a separate destination outside the venv:

```sh
export SRC="/path/to/kiro_crew/_vendor/llama_cpp_libs/macos_x86_64"
export DST="/path/to/patched-libs"
sh ./build_shim.sh
LLAMA_CPP_LIB_PATH="$DST" kirocrew gateway
```

Crew uses the patched copies through `LLAMA_CPP_LIB_PATH`. This uses dependency re-exports rather than the CLI's flat namespace injection.

## Checks and limits

My gateway loads the patched libraries and embedding model. The installed BLAS forwarder also passed a 2x2 matrix multiplication check. I have not compared full embedding vectors against a reference.

The forwarder silently returns if it cannot find the system `cblas_sgemm`. A clean startup log alone does not test that path. Check the libraries and launcher override again after a Crew update.

[Full case](../../../cases/kiro-bigsur-missing-symbols.md)

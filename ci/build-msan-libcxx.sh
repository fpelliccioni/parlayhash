#!/usr/bin/env bash
set -euo pipefail
: "${MSAN_LIBCXX_ROOT:?Set MSAN_LIBCXX_ROOT}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git clone --depth 1 --branch llvmorg-21.1.8 --filter=blob:none --sparse https://github.com/llvm/llvm-project.git "$work/llvm"
git -C "$work/llvm" sparse-checkout set runtimes libcxx libcxxabi cmake llvm/cmake llvm/utils libc compiler-rt/include
cmake -S "$work/llvm/runtimes" -B "$work/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-21 -DCMAKE_CXX_COMPILER=clang++-21 \
  '-DLLVM_ENABLE_RUNTIMES=libcxx;libcxxabi' -DLLVM_USE_SANITIZER=MemoryWithOrigins \
  -DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF -DLIBCXXABI_USE_LLVM_UNWINDER=OFF \
  -DLLVM_INCLUDE_TESTS=OFF -DLIBCXX_INCLUDE_TESTS=OFF \
  -DLIBCXXABI_INCLUDE_TESTS=OFF -DLIBCXX_INCLUDE_BENCHMARKS=OFF \
  -DCMAKE_CXX_FLAGS="-I$work/llvm/compiler-rt/include" -DCMAKE_INSTALL_PREFIX="$MSAN_LIBCXX_ROOT"
cmake --build "$work/build" --parallel 2
cmake --install "$work/build"

# Continuous integration and packages

The baseline is C++23. CI also builds examples, the consumer and concurrency
regressions in C++26 mode with Clang; this checks compiler support for this code,
not completeness of the C++26 standard library.

The GCC build, unit tests, benchmark smoke and publication consumer use the
[official GCC 16.2.0 image](https://hub.docker.com/_/gcc), pinned by manifest digest.
The container setup checks the effective compiler version. macOS uses the
[`xcode-27` ARM64 image](https://github.com/actions/runner-images/blob/main/images/macos/xcode-27-arm64-Readme.md)
with [Xcode 27.2](https://developer.apple.com/xcode/system-requirements) explicitly
selected, rather than the image's default toolchain. Compiler versions are printed
in the logs; historical local measurements retain their recorded compiler versions.

The workflow runs on pull requests, main, release/hotfix branch pushes, and manual
requests. Linux GCC/Clang and macOS AppleClang check CMake installation and a
separate consumer. Conan 2.31.2 creates and consumes a header-only package on each
C++23 platform. Existing GoogleTest tests run separately. The benchmark job runs runner regression tests and
checks the comparison harness; hosted-runner throughput is not a performance gate.

ASan (including leaks), UBSan, TSan, LSan and MSan are independent required jobs.
MSan builds an instrumented [LLVM 21.1.8](https://github.com/llvm/llvm-project/releases/tag/llvmorg-21.1.8)
[libc++](https://libcxx.llvm.org/)/[libc++abi](https://libcxxabi.llvm.org/) and caches
that installation. The runtime build disables its own upstream tests and benchmarks;
Parlay regression scenarios still run against the instrumented libraries. This
avoids pulling compiler-development targets into the runtime-only build.
No sanitizer findings are suppressed or allowed to fail. The current library has
known race, alignment and replacement-leak findings documented in
`test/concurrency/README.md`; publication will remain blocked until repaired.

## Publishing

Following the release-branch policy used by utxo-z, only successful pushes to
`release/X.Y.Z` or `hotfix/X.Y.Z` publish `parlayhash/X.Y.Z`. Main, PRs, tags and
manual dispatches validate without publishing. All validation jobs must pass.

Set repository secrets `CONAN_LOGIN_USERNAME` and `CONAN_PASSWORD` to credentials
with upload permission at `https://packages.kth.cash/api/`. No credentials belong
in this checkout. `ci/publish.sh` starts with a clean Conan cache in CI, creates
and tests the package, uploads its exact recipe revision, then downloads that
revision and the header package into a new anonymous cache and runs the consumer
with `--build=never`. Both global and remote-specific credential environment
variables are removed before verification; network checks use
`core:non_interactive=True` so authentication cannot silently rescue a public
package download. Publication regression tests verify that boundary and reject
non-release events without contacting the server. A failed post-upload check fails the job; it does not remove
an uploaded revision.

## Local use

```sh
conan profile detect
conan create . -pr:h ci/conan-cpp23 -pr:b default

cmake -S . -B build -DPARLAYHASH_EXAMPLES=ON
cmake --build build
cmake --install build --prefix /tmp/parlay-install
cmake -S test_package -B build-consumer -DCMAKE_PREFIX_PATH=/tmp/parlay-install
cmake --build build-consumer
./build-consumer/consumer
```

CMake requires 3.25 or later. Its exported target is `ParlayHash::parlay`, from
`find_package(ParlayHash CONFIG REQUIRED)`, also used by Conan CMakeDeps.
Benchmarks and examples are opt-in (`PARLAYHASH_BENCHMARKS`, `PARLAYHASH_EXAMPLES`)
so installing the core library does not require Boost, jemalloc or GoogleTest.
The recipe packages all headers, including vendored Parlay headers, and has no
Boost dependency. The recipe's `set_version()` retains 1.0.0 as the local default while accepting
the version supplied by the release branch. CI also exports a non-default version
to exercise that path. Header installation and exported target paths both honor
`CMAKE_INSTALL_INCLUDEDIR`; a separate consumer checks a custom header directory
after relocating the installation prefix.

The earlier baseline used C++17 with GCC 15.2.1 on Linux x86-64, running on
an AMD Ryzen Threadripper PRO 5995WX (64 cores / 128 hardware threads).
New comparison builds use C++23 for both implementations; rebuild both before
collecting a new baseline and do not merge results from different standards.

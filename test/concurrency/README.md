# Concurrency regression and sanitizer tests

These standalone tests require [CMake](https://cmake.org/) 3.25+, a C++23
compiler and platform thread support. C++23 is the default; C++26 may be selected
with `-DCMAKE_CXX_STANDARD=26`. They use eight
ordinary `std::thread` callers with a common start gate. No test concurrently
destroys a container while another thread is using it. No sanitizer suppressions
are applied and [jemalloc](https://jemalloc.net/) is not linked into these executables.
`PARLAY_NUM_THREADS=4` intentionally limits the internal Parlay scheduler; it does
not limit the eight external `std::thread` callers. These are separate settings.

```sh
cmake -S test/concurrency -B build/tsan \
  -DCMAKE_CXX_COMPILER=clang++ -DSANITIZER=thread
cmake --build build/tsan -j 4
ctest --test-dir build/tsan --output-on-failure
```

Run all modes separately, preserving configuration/build/test logs:

```sh
python3 test/concurrency/run_sanitizers.py \
  --compiler clang++ --output /tmp/parlay-sanitizers
```

For a subset supported by the local toolchain, select modes explicitly:

```sh
python3 test/concurrency/run_sanitizers.py --compiler clang++ \
  --modes none address undefined thread --output /tmp/parlay-selected-sanitizers
```

The default still requests all six modes. Selecting a subset is not a clean result
for the excluded modes: `run-config.json` records requested and unrequested modes.
MSan is never silently skipped. The output directory must be new. The runner exits unsuccessfully if any build,
test or sanitizer configuration fails. A configuration failure is not a clean
sanitizer result. `summary.json` distinguishes the phase that failed.
Configure, build and whole-suite test commands have default wall-clock limits of
180, 600 and 900 seconds, adjustable with `--configure-timeout`, `--build-timeout`
and `--test-timeout`. On POSIX systems a timeout kills the command's process group,
including compiler/test children, preserves partial logs, records `PHASE_timeout`
and continues with the next requested mode. Use `--jobs` to limit build parallelism.

| Mode | Coverage |
| --- | --- |
| none | Logical invariants without instrumentation |
| [address](https://clang.llvm.org/docs/AddressSanitizer.html) | Out-of-bounds/use-after-free; enables leak detection on supported platforms |
| [undefined](https://clang.llvm.org/docs/UndefinedBehaviorSanitizer.html) | Undefined behavior, including alignment |
| [thread](https://clang.llvm.org/docs/ThreadSanitizer.html) | Unsynchronized accesses/data races |
| [leak](https://clang.llvm.org/docs/LeakSanitizer.html) | Separate LeakSanitizer run |
| [memory](https://clang.llvm.org/docs/MemorySanitizer.html) | Uninitialized reads; requires instrumented [libc++](https://libcxx.llvm.org/) |

For MSan, provide `--msan-libcxx-root /path/to/instrumented/libcxx` (or
`-DMSAN_LIBCXX_ROOT=...` when configuring manually). It is deliberately rejected
without an instrumented standard library; using the system library would not
provide a reliable full-program MSan check. Platform-specific sanitizers such as
HWASan are not interchangeable with these modes and are not exercised here.

Scenarios:

- `snapshot_api`: pair payloads, stale-tag rejection, sequential initialization,
  and unsigned version wrap.
- `atomic_padded_snapshot`: snapshot checksum plus a boolean in a type with padding.
- `padded_churn`: direct-entry churn with the padded payload.
- `atomic_snapshot`: writers/readers on a `big_atomic` payload with a redundant
  checksum, isolating the bucket snapshot mechanism.
- `direct_churn` / `indirect_churn`: simultaneous insert, remove and find on 64
  hot keys, validating returned payloads and successful-operation accounting.
- `direct_growth` / `indirect_growth`: writers force repeated growth while readers
  verify permanent keys and churn a hot key to exercise retirement/reuse.
- `upsert`: eight writers increment one key, requiring exactly 16,000 increments.
- `string_overflow_upsert`: the same heap-owning updates under a constant hash,
  with the target behind two other overflow nodes. Verifies unchanged neighboring
  entries survive failed-attempt cleanup and successful replacement.
- `string_upsert`: eight writers update a heap-owning string value and read it
  concurrently, requiring exactly 8,000 increments and an intact prefix.

Each scenario runs in a separate process and has a 120-second CTest timeout.
Direct and indirect storage are explicitly selected rather than inferred from
the payload type. Use `ctest --repeat until-fail:100 --test-dir build/tsan` to
increase scheduling coverage after the first failures have been addressed.

Historical Linux x86-64 validation on Parlay main `9bbb167` (2026-11-12),
with [Clang 21.1.8](https://github.com/llvm/llvm-project/releases/tag/llvmorg-21.1.8) and **C++17**. These results
precede the C++23 build requirement; they are not results for the current C++23
configuration:

| Mode | Result across seven scenarios |
| --- | --- |
| Plain | 7 passed |
| ASan, with leak detection | 6 passed; `string_upsert` leaked |
| UBSan | Snapshot passed; all 6 map scenarios failed on bucket alignment |
| TSan | All 7 reported data races, including isolated `big_atomic` |
| LSan | 6 passed; `string_upsert` leaked about 1.57 MB |
| MSan | 7 passed, using instrumented libc++ and libc++abi 21.1.8 |

Earlier seven-scenario C++23 validation (before adding the overflow witness) with [GCC 15.2.1](https://gcc.gnu.org/gcc-15/) on Linux
x86-64, [AMD Ryzen Threadripper PRO 5995WX](https://www.amd.com/en/support/downloads/drivers.html/processors/ryzen-threadripper-pro/ryzen-threadripper-pro-5000wx-series/amd-ryzen-threadripper-pro-5995wx.html)
(64 cores / 128 hardware threads):

| Mode | Result across seven scenarios |
| --- | --- |
| Plain | 7 passed |
| ASan, with leak detection | 6 passed; `string_upsert` leaked 1,574,696 bytes in 15,998 allocations |
| UBSan | Snapshot passed; 6 map scenarios reported bucket-alignment violations |
| TSan | All 7 reported data races, including isolated `big_atomic` |
| LSan | Build failed: the toolchain references a missing `liblsan.so.0.0.0`; no test result |
| MSan | Not requested: this compiler/environment has no instrumented standard library for MSan |


## Findings and acceptance criteria

These findings need different library changes:

- **Alignment:** UBSan shows that the 64-byte-aligned bucket array is allocated
  with `malloc`. The allocator must provide the declared alignment and matching
  deallocation. A run can pass accidentally when an allocation happens to be
  aligned, so not every platform or scenario must fail on each run.
- **Indirect-entry lifetime:** the string-upsert witness reports leaked indirect
  entries. The suspected cause is missing retirement of the replaced entry in
  the inline buffer; a fix must establish correct ownership and reclamation,
  then pass leak checks under contention.
- **Optimistic snapshot / memory model:** `big_atomic` uses a seqlock-style
  optimistic read and subsequent version validation. That design explains the
  report but does not legalize concurrent non-atomic accesses under the
  [C++ data-race rules](https://eel.is/c++draft/intro.races). Making this check
  green requires a payload-access protocol with a valid synchronization proof,
  such as suitable atomic operations or exclusion covering both readers and
  writers. It is a design-level correctness change, not an allocator fix.
  Suppression or a sanitizer annotation alone does not remove C++ undefined
  behavior. An annotation would need independently justified synchronization
  that the tool cannot otherwise observe.

The independent sanitizer jobs keep these distinctions visible. Merging the
regression tests is separate from accepting the library for publication; the
CI/publication policy in PR #3 requires the checks to pass. Selecting fewer modes
locally does not weaken that policy.

These are regression witnesses: the original revision fails, and findings remain
failures until their library fixes are applied.
Passing a finite stress run never proves absence of races, and pool-based memory
reclamation may hide some logical use-after-free bugs from ASan.

For the MSan standard library, the [LLVM 21.1.8](https://github.com/llvm/llvm-project/releases/tag/llvmorg-21.1.8) runtimes were built with
`LLVM_ENABLE_RUNTIMES=libcxx;libcxxabi`, `LLVM_USE_SANITIZER=MemoryWithOrigins`,
`LLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF`, and `LIBCXXABI_USE_LLVM_UNWINDER=OFF`.
The test executable was checked to load the instrumented libc++/libc++abi, and
the library exports reference MSan runtime hooks. The ordinary system C++
library was not used for that run.

Runner regression tests use fixture tools rather than compiling the map:

```sh
python3 test/concurrency/test_runner.py
```

Worker exceptions are captured, all workers are joined, and the exception is
reported by the scenario process with its name. String-counter parsing checks
the prefix and entire numeric suffix without relying on `std::stoul` exceptions.
A malformed value sets the failure flag and returns a valid fallback string from
the update callback; the scenario reports corruption after joining all workers.
Validation deliberately does not throw through an in-progress library update.

With the upsert-retirement fix, all eight scenarios passed plain execution, ASan
with leak detection, and UBSan with GCC 15.2.1 / C++23 on the x86-64 configuration
above. The overflow witness leaked with the inline-only fix and passed after
retiring both replaced entries and entries constructed by failed attempts.

## Race-free optimistic snapshots

The snapshot implementation keeps the existing writer serialization and version
validation. Readers perform only atomic loads on shared data; they do not acquire
a writer lock or increment a shared reader counter. The common integer-map,
indirect-map and padded-map buckets are checked to remain 64 bytes. There is no
snapshot allocation on the update path.

Payload storage is encoded explicitly: pair members are separate, bucket state
contains atomic metadata and entry slots, and tagged indirect pointers use a
single pointer atomic. Trivially copyable leaf values use native integral atomic
lanes where object representation and alignment permit; padding-bearing types
use unsigned-byte atomics. Private representations are converted back to typed
values only after successful validation. Each publication stores a defined empty
entry in inactive slots; fixed-size reads therefore never reconstruct stale
reclaimed pointers from unused slots. Pack expansion and the existing inlining
hint keep the hot lookup from using metadata-dependent copy loops.

The memory ordering follows the original fence-based protocol, with **atomic**
payload accesses added:

1. A serialized writer marks the version odd, issues a release fence, performs
   relaxed atomic payload stores, then publishes an even version with release.
2. A reader acquires an even version, loads the representation with relaxed
   atomics, issues an acquire fence, and checks the version again.
3. If a payload load observes a later writer's store, that store connects the
   release/acquire fences, so the later writer's odd version happens-before the
   final version check. The reader cannot accept the older version. The initial
   acquire also prevents accepting payload older than its committed version.

See the [C++ fence rules](https://eel.is/c++draft/atomics.fences) and
[atomic coherence rules](https://eel.is/c++draft/intro.races). As with finite
version-tag protocols, one operation must not span a full counter cycle. The
counter is unsigned so incrementing across its maximum is defined.

The legacy internal `ll_speculative` entry point now returns a validated value;
it is not used by map operations. Trivially copyable payloads and pair/bucket
representations are supported; nontrivial map values still use indirect entries.
After 64 failed observations, readers yield cooperatively before retrying.

[TSan](https://clang.llvm.org/docs/ThreadSanitizer.html) does not fully model
`atomic_thread_fence`; passing it does not prove this ordering argument. Shared
payload accesses are actual atomics, not suppressed or annotated plain accesses.
The checksum/invariant tests and the memory-order reasoning are both necessary.

After the snapshot fix, all **11 scenarios pass all six modes** (plain, ASan,
UBSan, TSan, LSan and MSan) with Clang 21.1.8 / C++23 on the x86-64 hardware
configuration above. MSan uses an instrumented libc++/libc++abi 21.1.8. The added
checks cover padded values, direct padded-entry churn, pair representation,
stale tags and defined unsigned-counter wrap.

A short before/after diagnostic used the same GCC 15.2.1 C++23 Release build,
[jemalloc](https://jemalloc.net/), 100,000 entries, 1/32/128 threads, 0%/50% updates,
and uniform/Zipf 0.99 keys. Each of 12 cells used four alternating process pairs,
one measured 0.25-second round plus warmup per process. Across these 96 samples,
median throughput ratios (new/old) ranged from 1.02 to 1.15. This checks this
change against the version with alignment/reclamation fixes already applied;
it is not a general performance claim or a replacement for longer large-set runs.
The existing inlining hint and fixed-size representation avoid extra intermediate
state materialization on the hot read path.

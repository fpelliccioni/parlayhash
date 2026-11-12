# Parlay versus Boost concurrent_flat_map

Both executables compile the same `benchmarks/test_map.cpp`, using the adapters
in `other/parlay_hash` and `other/boost_hash`. Boost is pinned to the latest stable
release as of 2026-11-12, **1.92.0**, downloaded with SHA-256 verification. To test
another release or a prepared Boost checkout, pass `-DBOOST_DIR=/path/to/headers`.
The old experimental `poc/latch_free_group` and replacement headers from Slack
are not used.

```sh
cmake -S benchmarks/compare -B build/compare -DCMAKE_BUILD_TYPE=Release
cmake --build build/compare -j 4
python3 benchmarks/compare/run.py --build-dir build/compare \
  --output results/baseline --threads 1 2 4 8 16 32 48 64
```

This build needs CMake 3.25+, C++23, Python 3, and jemalloc headers/library.
On systems without jemalloc, `-DCOMPARE_JEMALLOC=OFF` permits functional checks;
`mem_pe` then has no meaning. Use the same allocator configuration for comparisons.
Only the two map executables are built; Folly, TBB, GoogleTest and Abseil are not
required. Build output records Boost's version, compiler, effective configuration flags
and allocator. With multi-configuration generators, build with `--config Release`
(or the desired configuration) and pass its executable directory, for example
`--build-dir build/compare/Release`; each configuration has its own metadata.

The runner's default matrix is:

| Parameter | Values |
| --- | --- |
| Keys and values | unsigned long keys, one unsigned long value |
| Initial entries | 100,000 and 10,000,000 |
| Update percentage | 0, 5, 50, 100 |
| Key distribution | uniform (`z=0`), Zipf (`z=0.99`) |
| Measured rounds | 5, with a separate warmup |
| Duration per round | 2 seconds |

Updates mean **half insertions, half deletions**, with the remaining operations
being lookups. Thus 5% updates means 2.5% insert, 2.5% erase, 95% find. This is
not value replacement/upsert. Initial population separately measures parallel
insertion throughput (`insert_mops`). CFM lookups use `cvisit` and copy the value,
matching Parlay's value-returning lookup semantics. Both adapters receive the
same hash, key generation, operation generation, scheduler and worker count.
Concurrent scheduling can still produce different interleavings and hit rates.

The default reserves for the initial population. Repeat with `--grow` in a
different output directory to exercise growth from minimal capacity. The mixed
insertion/deletion workload maintains approximately constant population; it is
not an insert-only growth-throughput benchmark.

`PARLAY_NUM_THREADS` is set to the requested `-p` for both implementations.
Each sample is a pair of fresh processes, each with one measured round and
its own warmup. Within each workload cell the runner alternates which map goes
first, and it also alternates the initial order between cells. With an odd number
of samples, one map necessarily starts one extra pair. The CSV records the paired
round, actual execution order, position within the pair and unique raw log.
All measured rounds are retained. It writes `results.csv`, raw logs and `metadata.json`
(source hashes, Git revision/diff when available, host and build configuration,
CPU model/topology and inherited effective CPU affinity where available). No
per-worker affinity is imposed; unavailable placement information is explicit.
Use a fresh output directory for each run. Metadata labels the sampling protocol
`paired-process-rounds-v1`; older cell-level-alternation results, including the
initial C++17 baseline on Linux x86-64 with GCC 15.2.1 and an AMD Ryzen
Threadripper PRO 5995WX (64 cores / 128 hardware threads), must remain separate. Preserve the build directory alongside
results so that the binaries and downloaded Boost headers remain available.

The existing driver also runs a separate int-set workload. It remains in raw logs;
the comparison CSV selects only the equivalent `long_long` map workload. The
runner stops on process failure, size inconsistencies, workload mismatches
(including Zipf and growth), or missing measured rounds. Each executable has a
600-second wall-clock timeout including setup and warmup; override it with
`--sample-timeout SECONDS` for larger datasets. A timeout preserves partial output
and stops the run without adding the failed sample to the CSV.

For a short functional check:

```sh
python3 benchmarks/compare/run.py --build-dir build/compare \
  --output results/smoke --threads 1 4 --sizes 1000 \
  --updates 0 5 50 100 --rounds 1 --seconds 0.05
```

These short checks are not performance conclusions. For performance runs, use an
otherwise idle machine, consistent CPU affinity/NUMA policy and enough duration
and repetitions. Plot median Mops/s versus threads with spread across rounds,
separating panels by size, update percentage, distribution and growth mode.

Generate PNG and SVG plots (requires `python3 -m pip install matplotlib` in your
analysis environment):

```sh
python3 benchmarks/compare/plot.py results/baseline/results.csv --output results/baseline/plots
```

The plotter shows medians with min/max bars across measured rounds; the bars are
not confidence intervals. Use `--metric insert_mops` for initial insertion plots.

Runner regression checks (no performance workloads):

```sh
python3 benchmarks/compare/test_run.py
```

The standalone build does not add `-march=native`; it uses the selected compiler's
configuration defaults and the flags explicitly supplied to CMake. The current
root benchmark CMake also does not add `-march=native`. Results from different
compiler flags or build configurations must remain separate. For host-specific
measurements, pass `-DCMAKE_CXX_FLAGS=-march=native` to a fresh build of both maps
and preserve its generated metadata.

In the root benchmark build, Boost is opt-in: pass `-DBOOST_DIR=/path/to/boost`.
The old hardcoded Boost 1.83 build-directory lookup is no longer used.
Throughput fields are floating-point numbers and may use scientific notation;
consumers must not assume integer-only output.

#!/usr/bin/env python3
"""Run the same test_map workload for Parlay and Boost CFM; preserve raw output."""
import argparse
import csv
import itertools
import json
import math
import hashlib
import os
from pathlib import Path
import platform
import subprocess
from datetime import datetime, timezone


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def hardware_metadata():
    """Record runtime hardware and inherited placement, with explicit gaps."""
    def query(command):
        try:
            result = subprocess.run(command, text=True, capture_output=True,
                                    env=dict(os.environ, LC_ALL="C"), timeout=10)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    hardware = {"logical_cpus": os.cpu_count(), "cpu_model": None,
                "topology": None, "effective_affinity": None,
                "placement_policy": "Inherited process placement; no per-worker pinning"}
    if hasattr(os, "sched_getaffinity"):
        try:
            hardware["effective_affinity"] = sorted(os.sched_getaffinity(0))
        except OSError:
            pass
    if platform.system() == "Linux":
        summary = query(["lscpu", "--json"])
        topology = query(["lscpu", "--json", "--extended=CPU,CORE,SOCKET,NODE,ONLINE"])
        if summary:
            entries = json.loads(summary)["lscpu"]
            hardware["cpu_model"] = next((x["data"] for x in entries
                                          if x["field"] == "Model name:"), None)
            hardware["cpu_summary"] = entries
        if topology:
            hardware["topology"] = json.loads(topology)
    elif platform.system() == "Darwin":
        hardware["cpu_model"] = query(["sysctl", "-n", "machdep.cpu.brand_string"])
        hardware["topology"] = {
            key: query(["sysctl", "-n", key]) for key in
            ("hw.logicalcpu", "hw.physicalcpu", "hw.packages")}
    if hardware["effective_affinity"] is None:
        hardware["affinity_note"] = "Effective affinity unavailable on this platform"
    return hardware


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=positive, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--sizes", type=positive, nargs="+", default=[100000, 10000000])
    parser.add_argument("--updates", type=int, choices=range(101), metavar="PERCENT",
                        nargs="+", default=[0, 5, 50, 100])
    parser.add_argument("--zipfians", type=float, nargs="+", default=[0, .99])
    parser.add_argument("--rounds", type=positive, default=5)
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--sample-timeout", type=float, default=600,
                        help="wall-clock limit per executable, including setup and warmup (seconds)")
    parser.add_argument("--grow", action="store_true", help="start empty with minimal capacity")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0 or not all(0 <= z < 1 for z in args.zipfians):
        parser.error("seconds must be finite and positive and Zipf parameters must be in [0, 1)")
    if not math.isfinite(args.sample_timeout) or args.sample_timeout <= 0:
        parser.error("sample timeout must be finite and positive")
    build = args.build_dir.resolve()
    root = Path(__file__).resolve().parents[2]
    maps = ("parlay_hash", "boost_hash")
    if not args.dry_run:
        for name in maps:
            if not (build / name).is_file():
                parser.error("missing executable: " + str(build / name))
        args.output.mkdir(parents=True, exist_ok=False)
        def git(*arguments):
            result = subprocess.run(["git", *arguments], cwd=root, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return result.stdout.strip() if result.returncode == 0 else None

        source_files = [root / "benchmarks/test_map.cpp"]
        for directory in ("include", "other/parlay_hash", "other/boost_hash", "benchmarks/compare"):
            source_files.extend(p for p in (root / directory).rglob("*")
                                if p.is_file() and "__pycache__" not in p.parts)
        metadata = dict(vars(args), build_dir=str(build), output=str(args.output),
                        host=platform.node(), platform=platform.platform(),
                        hardware=hardware_metadata(),
                        sampling_protocol="paired-process-rounds-v1",
                        utc=datetime.now(timezone.utc).isoformat(),
                        parlay_commit=git("rev-parse", "HEAD"),
                        parlay_diff=git("diff", "HEAD"),
                        source_sha256={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in source_files},
                        build=(build / "comparison-build.txt").read_text())
        (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    fields = ["implementation", "n", "p", "update", "z", "grow", "round",
              "mops", "insert_mops", "mem_pe", "execution_order", "pair_position", "log"]
    # Run one measured round per process and alternate order within each cell.
    execution_order = 0
    cells = itertools.product(args.sizes, args.updates, args.zipfians, args.threads)
    rows = []
    for index, (n, u, z, p) in enumerate(cells):
        for sample in range(1, args.rounds + 1):
            order = maps[::(-1 if (index + sample - 1) % 2 else 1)]
            for pair_position, name in enumerate(order, 1):
                execution_order += 1
                command = [str(build / name), "-n", str(n), "-u", str(u),
                           "-z", str(z), "-p", str(p), "-r", "1",
                           "-t", str(args.seconds), "-nostring", "-nomeans"]
                if args.grow:
                    command.append("-grow")
                print("PARLAY_NUM_THREADS=" + str(p), *command, flush=True)
                if args.dry_run:
                    continue
                env = dict(os.environ, PARLAY_NUM_THREADS=str(p))
                log = args.output / f"{index:04d}-sample{sample:03d}-{name}.log"
                try:
                    result = subprocess.run(command, env=env, cwd=root, text=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            timeout=args.sample_timeout)
                except subprocess.TimeoutExpired as error:
                    partial = error.stdout or b""
                    if isinstance(partial, bytes):
                        partial = partial.decode("utf-8", errors="replace")
                    log.write_text(partial + f"\nRunner timeout after {args.sample_timeout}s\n")
                    raise RuntimeError(f"benchmark timed out; inspect {log}") from error
                log.write_text(result.stdout)
                if result.returncode or "bad initial size" in result.stdout or "bad final size" in result.stdout:
                    raise RuntimeError(f"benchmark failed; inspect {log}")
                selected = []
                for line in result.stdout.splitlines():
                    if ",long_long," not in line or ",mops=" not in line:
                        continue  # test_map also emits its separate int-set workload
                    values = dict(part.split("=", 1) for part in line.split(",") if "=" in part)
                    if any(values.get(key) != expected for key, expected in
                           (("n", str(n)), ("p", str(p)), ("update", str(u) + "%"),
                            ("grow", str(int(args.grow))))):
                        raise RuntimeError(f"unexpected workload; inspect {log}")
                    try:
                        reported_z = float(values["z"])
                    except (KeyError, ValueError) as error:
                        raise RuntimeError(f"missing or invalid Zipf parameter; inspect {log}") from error
                    if reported_z != z:
                        raise RuntimeError(f"unexpected Zipf parameter; inspect {log}")
                    selected.append(dict(values, implementation=name, round=sample,
                                         execution_order=execution_order,
                                         pair_position=pair_position, log=log.name))
                if len(selected) != 1:
                    raise RuntimeError(f"expected one measured round; inspect {log}")
                rows.extend(selected)
                with (args.output / "results.csv").open("w", newline="") as output:
                    writer = csv.DictWriter(output, fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows)


if __name__ == "__main__":
    main()

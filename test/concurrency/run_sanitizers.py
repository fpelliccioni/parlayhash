#!/usr/bin/env python3
"""Build and run each sanitizer separately, retaining failures and unsupported modes."""
import argparse
import json
import os
import signal
from pathlib import Path
import subprocess
import sys


MODES = ("none", "address", "undefined", "thread", "leak", "memory")


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def run_phase(command, log, timeout):
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=(os.name == "posix"))
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--msan-libcxx-root", type=Path)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--jobs", type=positive, default=4)
    parser.add_argument("--configure-timeout", type=positive, default=180)
    parser.add_argument("--build-timeout", type=positive, default=600)
    parser.add_argument("--test-timeout", type=positive, default=900)
    args = parser.parse_args()
    if len(set(args.modes)) != len(args.modes):
        parser.error("modes must not contain duplicates")
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parent
    summary = {}
    (args.output / "run-config.json").write_text(json.dumps({
        "requested_modes": args.modes,
        "not_requested": [mode for mode in MODES if mode not in args.modes],
        "compiler": args.compiler,
        "jobs": args.jobs,
        "phase_timeouts_seconds": {
            "configure": args.configure_timeout,
            "build": args.build_timeout,
            "test": args.test_timeout,
        },
    }, indent=2))
    for mode in args.modes:
        build = (args.output / mode).resolve()
        configure = ["cmake", "-S", str(source), "-B", str(build),
                     "-DSANITIZER=" + mode, "-DCMAKE_CXX_COMPILER=" + args.compiler]
        if mode == "memory" and args.msan_libcxx_root:
            configure.append("-DMSAN_LIBCXX_ROOT=" + str(args.msan_libcxx_root.resolve()))
        status = "passed"
        for phase, timeout, command in (
            ("configure", args.configure_timeout, configure),
            ("build", args.build_timeout,
             ["cmake", "--build", str(build), "-j", str(args.jobs)]),
            ("test", args.test_timeout,
             ["ctest", "--test-dir", str(build), "--output-on-failure"]),
        ):
            with (args.output / f"{mode}-{phase}.log").open("w") as log:
                try:
                    returncode = run_phase(command, log, timeout)
                except subprocess.TimeoutExpired:
                    log.write(f"\nRunner timeout after {timeout}s\n")
                    status = phase + "_timeout"
                    break
                except OSError as error:
                    log.write(f"\nCould not execute command: {error}\n")
                    status = phase + "_failed"
                    break
            if returncode:
                status = phase + "_failed"
                break
        summary[mode] = status
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
        print(mode + ": " + status, flush=True)
    return 0 if all(status == "passed" for status in summary.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

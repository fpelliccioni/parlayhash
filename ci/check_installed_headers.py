#!/usr/bin/env python3
"""Verify header ownership in a CMake or Conan package without modifying it."""
import argparse
from pathlib import Path


def check(root):
    owned = root / "parlayhash"
    if not owned.is_dir():
        raise SystemExit(f"Missing packaged header root: {owned}")
    expected = {"parlay_hash", "parlay", "utils"}
    actual = {p.name for p in owned.iterdir()}
    if actual != expected:
        raise SystemExit(f"Unexpected packaged header directories: {actual!r}")
    for directory, header in (("parlay_hash", "unordered_map.h"),
                              ("parlay", "primitives.h"), ("utils", "epoch.h")):
        if not (owned / directory / header).is_file():
            raise SystemExit(f"Missing required header: {directory}/{header}")
    for legacy in owned.rglob("old_parlay_hash"):
        raise SystemExit(f"Legacy implementation packaged: {legacy}")
    print(f"PASS installed header layout: {owned}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("include_root", type=Path)
    check(parser.parse_args().include_root)

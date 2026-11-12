#!/usr/bin/env python3
"""Plot per-workload median throughput and min/max measured rounds."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metric", choices=("mops", "insert_mops"), default="mops")
    args = parser.parse_args()
    with args.csv.open() as source:
        rows = list(csv.DictReader(source))
    if not rows:
        parser.error("CSV has no measured rounds")
    args.output.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    for row in rows:
        key = (int(row["n"]), int(row["grow"]), int(row["update"].rstrip("%")),
               float(row["z"]), row["implementation"], int(row["p"]))
        groups[key].append(float(row[args.metric]))
    for n, grow in sorted({(k[0], k[1]) for k in groups}):
        updates = sorted({k[2] for k in groups if k[:2] == (n, grow)})
        zipfians = sorted({k[3] for k in groups if k[:2] == (n, grow)})
        fig, axes = plt.subplots(len(updates), len(zipfians), squeeze=False,
                                 figsize=(6 * len(zipfians), 3.5 * len(updates)))
        for i, u in enumerate(updates):
            for j, z in enumerate(zipfians):
                ax = axes[i][j]
                for name, label, color in (("parlay_hash", "Parlay main", "tab:blue"),
                                           ("boost_hash", "Boost CFM", "tab:red")):
                    points = sorted((k[5], values) for k, values in groups.items()
                                    if k[:5] == (n, grow, u, z, name))
                    if not points:
                        continue
                    x = [p for p, _ in points]
                    y = [median(v) for _, v in points]
                    error = [[m - min(v) for m, (_, v) in zip(y, points)],
                             [max(v) - m for m, (_, v) in zip(y, points)]]
                    ax.errorbar(x, y, yerr=error, label=label, color=color,
                                marker="s", capsize=3)
                    ax.set_xticks(x)
                ax.set_title(f"{u}% updates, z={z:g}")
                ax.set_xlabel("Threads")
                ax.set_ylabel("Mops/s" if args.metric == "mops" else "Initial insert Mops/s")
                ax.set_ylim(bottom=0)
                ax.grid(alpha=.3)
                ax.legend()
        fig.suptitle(f"n={n:,}, grow={grow} — median; bars: min/max across rounds\n"
                     "Single-machine measurements; see metadata.json for versions and settings")
        fig.tight_layout(rect=(0, 0, 1, .95))
        for extension in ("png", "svg"):
            fig.savefig(args.output / f"{args.metric}-n{n}-grow{grow}.{extension}", dpi=150)
        plt.close(fig)


if __name__ == "__main__":
    main()

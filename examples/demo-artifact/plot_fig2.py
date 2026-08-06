"""Render Figure 2 — the demo's headline result — from the trained metrics."""

from __future__ import annotations

import argparse
import json

import matplotlib.pyplot as plt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--metrics", default="outputs/metrics.json")
    ap.add_argument("--out", default="fig2.png")
    args = ap.parse_args()

    with open(args.metrics) as f:
        acc = json.load(f)["accuracy"]

    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar(["demo model"], [acc])
    ax.set_ylim(0, 1)
    ax.set_ylabel("test accuracy")
    ax.set_title("Figure 2 — reproduced result")
    fig.tight_layout()
    fig.savefig(args.out, dpi=150)


if __name__ == "__main__":
    main()

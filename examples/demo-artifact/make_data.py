"""Generate the synthetic demo dataset from the config."""

from __future__ import annotations

import argparse

import numpy as np
from sklearn.datasets import make_blobs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="data/blobs.npz")
    args = ap.parse_args()

    x, y = make_blobs(n_samples=2000, n_features=20, centers=3, random_state=1234)
    np.savez(args.out, x=x, y=y)


if __name__ == "__main__":
    main()

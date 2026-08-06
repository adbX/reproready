"""Train the demo classifier and save its accuracy to a metrics file."""

from __future__ import annotations

import argparse
import json

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--data", default="data/blobs.npz")
    ap.add_argument("--out", default="outputs/metrics.json")
    args = ap.parse_args()

    d = np.load(args.data)
    x_train, x_test, y_train, y_test = train_test_split(
        d["x"], d["y"], test_size=0.2, random_state=1234
    )
    clf = LogisticRegression(max_iter=1000).fit(x_train, y_train)
    acc = float(clf.score(x_test, y_test))
    with open(args.out, "w") as f:
        json.dump({"accuracy": acc}, f)
    print(f"test accuracy: {acc:.3f}")


if __name__ == "__main__":
    main()

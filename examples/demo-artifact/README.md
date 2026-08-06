# Demo artifact

A tiny, fully synthetic project that ReproReady scores well. It exists to show
what a reproduction-ready archive looks like: pinned environment, obtainable
inputs, a one-command driver, and a clear map from the code to the reported
figure.

## Setup

Install the pinned dependencies:

```sh
pip install -r requirements.txt
```

All versions are pinned in `requirements.txt`, so the environment is
reconstructed exactly.

## Data

The dataset is generated deterministically (no network needed):

```sh
bash download.sh
```

This writes `data/blobs.npz`. The generation parameters live in `config.yaml`
(`seed`, `n_samples`, `n_features`, `n_classes`).

## Running

Reproduce everything with a single command:

```sh
bash run_all.sh
```

`run_all.sh` fetches the data, runs `train.py`, and renders the figure with
`plot_fig2.py`. Each step reads its settings from `config.yaml`.

## Results

The headline result is **Figure 2** (`fig2.png`), a bar chart of the demo
model's held-out test accuracy. It is produced by `plot_fig2.py` from the
metrics written by `train.py`. Re-running `run_all.sh` regenerates the same
figure, closing the loop from code back to the reported result.

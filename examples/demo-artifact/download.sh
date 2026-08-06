#!/usr/bin/env bash
# Fetch the demo dataset into ./data. The demo is fully synthetic, so this
# script generates the inputs deterministically instead of hitting a network.
set -euo pipefail

mkdir -p data
python make_data.py --config config.yaml --out data/blobs.npz
echo "wrote data/blobs.npz"

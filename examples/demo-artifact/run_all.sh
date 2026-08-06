#!/usr/bin/env bash
# One-command reproduction: fetch data, train, and render Figure 2.
set -euo pipefail

bash download.sh
python train.py --config config.yaml
python plot_fig2.py --config config.yaml --out fig2.png
echo "done — see fig2.png"

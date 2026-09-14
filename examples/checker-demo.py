#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Clean-room input used to demonstrate the static checker report."""

from pathlib import Path
from sys import path

# Download https://example.invalid/research-data.zip before analysis.
DATA_PATH = Path("/example/data.csv")
path.append(".../vendor")


if __name__ == "__main__":
    print(DATA_PATH)

"""Tests for the deterministic content summarisers.

Checks the file-type router, verbatim line-numbering, Python AST compression
(whitelist call-sites + signatures kept, true line numbers preserved), notebook
output-dropping, data-stat path+size, and byte-identical determinism.
"""

from __future__ import annotations

import json

from reproready.extract import (
    EXTRACT_VERSION,
    extract_evidence,
    kind_for,
)


def test_kind_router() -> None:
    assert kind_for("requirements.txt") == "manifest"
    assert kind_for("environment.yml") == "manifest"
    assert kind_for("config.yaml") == "manifest"
    assert kind_for("install.sh") == "shell"
    assert kind_for("Makefile") == "shell"
    assert kind_for("model.py") == "python"
    assert kind_for("nb.ipynb") == "notebook"
    assert kind_for("README.md") == "readme"
    assert kind_for("data/train.csv") == "data_stat"
    assert kind_for("solver.R") == "other"


def test_verbatim_line_numbered() -> None:
    body = b"torch==1.13.1\nnumpy==1.24.0\n"
    ev = extract_evidence("requirements.txt", body, len(body))
    assert ev.kind == "manifest"
    assert ev.content == "1: torch==1.13.1\n2: numpy==1.24.0\n3: "
    assert not ev.truncated


def test_python_small_is_verbatim() -> None:
    body = b"import os\n\ndef main():\n    print('hi')\n"
    ev = extract_evidence("main.py", body, len(body))
    assert ev.kind == "python"
    assert not ev.truncated
    assert "1: import os" in ev.content


def test_python_ast_compression_keeps_signal() -> None:
    # A large file: filler functions push it over the cap, forcing compression.
    filler = "\n".join(
        f"def filler_{i}():\n    a = {i}\n    b = a + 1\n    return b\n"
        for i in range(400)
    )
    src = (
        '"""Module docstring."""\n'
        "import argparse\n"
        "import matplotlib.pyplot as plt\n" + filler + "\n"
        "def plot_figure():\n"
        "    fig = plt.figure()\n"
        "    plt.savefig('fig2.pdf')\n"
    )
    body = src.encode()
    ev = extract_evidence("plot_fig.py", body, len(body), token_cap=200)
    assert ev.truncated
    # Imports, the savefig call-site, and def headers survive; filler bodies don't.
    assert "import argparse" in ev.content
    assert "plt.savefig('fig2.pdf')" in ev.content
    assert "def plot_figure" in ev.content
    assert "elided" in ev.content
    # Filler *bodies* (the `b = a + 1` lines) should be largely gone.
    assert ev.content.count("b = a + 1") < 50


def test_python_true_line_numbers_preserved() -> None:
    filler = "\n".join(f"x{i} = {i}" for i in range(500))
    src = filler + "\nimport torch\ntorch.save(model, 'ckpt.pt')\n"
    body = src.encode()
    ev = extract_evidence("train.py", body, len(body), token_cap=100)
    # torch.save is on line 502; its true line number must appear.
    assert "502: torch.save(model, 'ckpt.pt')" in ev.content


def test_python_syntax_error_falls_back() -> None:
    # Python-2 print statement: unparseable under py3 → verbatim head+tail.
    body = b"print 'hello'\n" + b"x = 1\n" * 2000
    ev = extract_evidence("legacy.py", body, len(body), token_cap=100)
    assert ev.kind == "python"
    assert ev.truncated


def test_notebook_drops_outputs() -> None:
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n", "intro"]},
            {
                "cell_type": "code",
                "source": ["import numpy as np\n", "np.save('x', a)\n"],
                "outputs": [{"text": "SHOULD NOT APPEAR 12345"}],
            },
        ]
    }
    body = json.dumps(nb).encode()
    ev = extract_evidence("analysis.ipynb", body, len(body))
    assert ev.kind == "notebook"
    assert "SHOULD NOT APPEAR" not in ev.content
    assert "# Title" in ev.content
    assert "np.save" in ev.content


def test_data_stat_path_size_only() -> None:
    ev = extract_evidence("data/big.npy", None, 5_000_000)
    assert ev.kind == "data_stat"
    assert "data/big.npy" in ev.content
    assert "5000000 bytes" in ev.content
    assert ev.n_lines_orig == 0


def test_data_stat_small_csv_header() -> None:
    body = b"col1,col2\n1,2\n3,4\n"
    ev = extract_evidence("data/small.csv", body, len(body))
    assert "col1,col2" in ev.content
    assert "data/small.csv" in ev.content


def test_determinism() -> None:
    body = ("import os\n" + "def f():\n    return 1\n" * 1000).encode()
    a = extract_evidence("m.py", body, len(body), token_cap=150)
    b = extract_evidence("m.py", body, len(body), token_cap=150)
    assert a == b
    assert EXTRACT_VERSION == "extract-v1"

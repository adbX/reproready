"""Tests for the versioned basename → cell routing table (2-channel).

The invariants: one path → at most one cell; every routed file lands on the
single ``implementation`` channel; manifests/scripts beat the computational
catch-all; computational modules land in ``X/implementation`` (weak credit);
junk/README are not routed.
"""

from __future__ import annotations

import pytest

from reproready.routing import (
    IMPLEMENTATION,
    Cell,
    classify,
    is_junk,
    is_renderable_readme,
    is_runnable_unit,
)


@pytest.mark.parametrize(
    "path,stage",
    [
        # Environment — manifests
        ("requirements.txt", "E"),
        ("requirements-dev.txt", "E"),
        ("environment.yml", "E"),
        ("conda_env.yaml", "E"),
        ("Dockerfile", "E"),
        ("Dockerfile.gpu", "E"),
        ("pyproject.toml", "E"),
        ("setup.py", "E"),
        ("poetry.lock", "E"),
        ("Singularity.def", "E"),
        # Environment — provisioning scripts
        ("install.sh", "E"),
        ("setup.sh", "E"),
        ("build.sh", "E"),
        # Inputs — acquisition + preprocessing scripts
        ("download_data.py", "I"),
        ("get_data.sh", "I"),
        ("preprocess.py", "I"),
        ("prepare_dataset.py", "I"),
        # Validation — eval / plot / analyze scripts
        ("eval.py", "V"),
        ("evaluate.py", "V"),
        ("plot_results.py", "V"),
        ("analyze.py", "V"),
        # Execution — drivers
        ("run_all.sh", "X"),
        ("run_experiments.py", "X"),
        ("reproduce.py", "X"),
        ("train.py", "X"),
        # Validation — producers + outputs
        ("table2.py", "V"),
        ("figure_3.ipynb", "V"),
        ("generate_figures.py", "V"),
        ("final_results.csv", "V"),
        ("expected_output.txt", "V"),
        ("results/", "V"),
        # Execution — entry points + computational modules
        ("main.py", "X"),
        ("run.py", "X"),
        ("Makefile", "X"),
        ("dvc.yaml", "X"),
        ("model.py", "X"),
        ("losses.py", "X"),
        ("src/data_loader.py", "X"),
        ("notebook.ipynb", "X"),
        # Inputs — configs + data
        ("config.yaml", "I"),
        ("configs/base.json", "I"),
        ("data/train.csv", "I"),
        ("dataset.npy", "I"),
        ("download_links.txt", "I"),
    ],
)
def test_classify(path: str, stage: str) -> None:
    assert classify(path, is_dir=path.endswith("/")) == Cell(stage, IMPLEMENTATION)


def test_every_routed_file_is_code_channel() -> None:
    for path in ("requirements.txt", "main.py", "config.yaml", "plot.py", "results/"):
        cell = classify(path, is_dir=path.endswith("/"))
        assert cell is not None and cell.channel == IMPLEMENTATION


def test_computational_module_is_execution_code() -> None:
    # A bare computational module is a runnable unit (X/implementation), weak credit.
    assert classify("model.py") == Cell("X", IMPLEMENTATION)
    assert classify("utils.py") == Cell("X", IMPLEMENTATION)
    assert classify("metrics.py") == Cell("X", IMPLEMENTATION)


def test_readme_not_routed() -> None:
    assert classify("README.md") is None
    assert classify("readme.rst") is None


def test_junk_not_routed() -> None:
    assert classify("__pycache__/model.cpython-311.pyc") is None
    assert classify("node_modules/foo/requirements.txt") is None
    assert classify(".git/config") is None
    assert is_junk("code.zip!ERROR:BadZipFile")


def test_unclassified_returns_none() -> None:
    assert classify("LICENSE") is None
    assert classify("notes.txt") is None
    assert classify("paper.pdf") is None


def test_renderable_readme() -> None:
    assert is_renderable_readme("README.md")
    assert is_renderable_readme("README")
    assert is_renderable_readme("readme.rst")
    assert not is_renderable_readme("README.pdf")
    assert not is_renderable_readme("model.py")


def test_runnable_unit() -> None:
    assert is_runnable_unit("model.py", is_dir=False)
    assert is_runnable_unit("notebook.ipynb", is_dir=False)
    assert not is_runnable_unit("data/", is_dir=True)
    assert not is_runnable_unit("config.yaml", is_dir=False)
    assert not is_runnable_unit("__pycache__/x.py", is_dir=False)

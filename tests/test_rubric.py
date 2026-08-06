"""Tests for the deterministic rubric detectors.

Pinned vs. unpinned (E), obtainable-data + real-config (I), orchestrator vs.
bare modules (X), the Validation floor, and the Docs cap at 0.5.
"""

from __future__ import annotations

from reproready import routing
from reproready.rubric import (
    GradeContext,
    grade_cell,
    strip_line_numbers,
)


def _ctx(**kw) -> GradeContext:
    base = dict(
        pointers={},
        text={},
        sizes={},
        readme_path="",
        has_readme=False,
        readme_dark=False,
        n_runnable=0,
    )
    base.update(kw)
    return GradeContext(**base)


def _grade(stage: str, channel: str, ctx: GradeContext) -> float:
    return grade_cell(routing.Cell(stage, channel), ctx).grade


# --- Environment -------------------------------------------------------------


def test_env_absent() -> None:
    assert _grade("E", "implementation", _ctx()) == 0.0


def test_env_present_unpinned() -> None:
    ctx = _ctx(
        pointers={"E": ["requirements.txt"]},
        text={"requirements.txt": "numpy\nscipy\n"},
    )
    assert _grade("E", "implementation", ctx) == 0.5


def test_env_strong_pinned() -> None:
    ctx = _ctx(
        pointers={"E": ["requirements.txt"]},
        text={"requirements.txt": "torch==1.13.1\nnumpy==1.24\n"},
    )
    assert _grade("E", "implementation", ctx) == 1.0


def test_env_strong_lockfile() -> None:
    ctx = _ctx(pointers={"E": ["poetry.lock"]}, text={"poetry.lock": ""})
    assert _grade("E", "implementation", ctx) == 1.0


def test_env_docker_latest_not_strong() -> None:
    ctx = _ctx(
        pointers={"E": ["Dockerfile"]}, text={"Dockerfile": "FROM python:latest\n"}
    )
    assert _grade("E", "implementation", ctx) == 0.5


def test_env_docker_pinned_strong() -> None:
    ctx = _ctx(
        pointers={"E": ["Dockerfile"]}, text={"Dockerfile": "FROM python:3.10-slim\n"}
    )
    assert _grade("E", "implementation", ctx) == 1.0


# --- Inputs ------------------------------------------------------------------


def test_inputs_data_only_no_config() -> None:
    # Bundled data but no real config → present, not strong.
    ctx = _ctx(
        pointers={"I": ["data/x.npy", "data/y.npy", "data/z.npy"]},
        sizes={"data/x.npy": 10, "data/y.npy": 10, "data/z.npy": 10},
    )
    assert _grade("I", "implementation", ctx) == 0.5


# A config with real values: >64 chars and ≥2 non-comment body lines.
_REAL_CFG = (
    "learning_rate: 0.1\nbatch_size: 128\nepochs: 200\noptimizer: adam\n"
    "weight_decay: 0.0005\nmodel: resnet50\n"
)


def test_inputs_data_only_short_config_not_strong() -> None:
    # Real data, but a boilerplate (too-short) config → present, not strong.
    ctx = _ctx(
        pointers={"I": ["config.yaml", "data/a.csv"]},
        text={"config.yaml": "lr: 0.1\n"},
        sizes={"data/a.csv": 200_000},
    )
    assert _grade("I", "implementation", ctx) == 0.5


def test_inputs_strong_data_and_config() -> None:
    ctx = _ctx(
        pointers={"I": ["config.yaml", "data/a.csv"]},
        text={"config.yaml": _REAL_CFG},
        sizes={"data/a.csv": 200_000},
    )
    assert _grade("I", "implementation", ctx) == 1.0


def test_inputs_download_script_counts_as_obtainable() -> None:
    ctx = _ctx(
        pointers={"I": ["download_data.py", "config.yaml"]},
        text={"config.yaml": _REAL_CFG},
    )
    assert _grade("I", "implementation", ctx) == 1.0


# --- Execution ---------------------------------------------------------------


def test_exec_bare_modules_present_only() -> None:
    ctx = _ctx(
        pointers={"X": ["model.py", "utils.py"]},
        text={"model.py": "class Net: pass\n", "utils.py": "def f(): pass\n"},
    )
    assert _grade("X", "implementation", ctx) == 0.5


def test_exec_orchestrator_file_strong() -> None:
    ctx = _ctx(
        pointers={"X": ["Makefile", "model.py"]},
        text={"Makefile": "all:\n\tpython main.py\n"},
    )
    assert _grade("X", "implementation", ctx) == 1.0


def test_exec_entrypoint_subprocess_strong() -> None:
    body = "import subprocess\nsubprocess.run(['python', 'train.py'])\n"
    ctx = _ctx(pointers={"X": ["main.py", "model.py"]}, text={"main.py": body})
    assert _grade("X", "implementation", ctx) == 1.0


# --- Validation (floor) ------------------------------------------------------


def test_validation_caps_at_floor() -> None:
    # A producer is present, but the deterministic floor never promotes V → 1.
    ctx = _ctx(
        pointers={"V": ["plot_fig2.py"]},
        text={"plot_fig2.py": "plt.savefig('fig2.pdf')\n"},
    )
    assert _grade("V", "implementation", ctx) == 0.5
    assert _grade("V", "implementation", _ctx()) == 0.0


# --- Docs (capped at 0.5) ----------------------------------------------------


def test_documentation_caps_at_half() -> None:
    readme = (
        "# Installation\npip install -r requirements.txt\n# Usage\npython main.py\n"
    )
    ctx = _ctx(readme_path="README.md", has_readme=True, text={"README.md": readme})
    assert _grade("E", "documentation", ctx) == 0.5  # install section present
    assert _grade("X", "documentation", ctx) == 0.5  # usage section present
    assert _grade("V", "documentation", ctx) == 0.0  # no results section


def test_docs_dark_is_zero() -> None:
    ctx = _ctx(readme_path="README.pdf", has_readme=True, readme_dark=True)
    assert _grade("E", "documentation", ctx) == 0.0


def test_docs_headerless_keyword_match() -> None:
    ctx = _ctx(
        readme_path="README.txt",
        has_readme=True,
        text={"README.txt": "To install, run pip install numpy.\n"},
    )
    assert _grade("E", "documentation", ctx) == 0.5


# --- helpers -----------------------------------------------------------------


def test_strip_line_numbers() -> None:
    numbered = "1: FROM python:3.10\n      … 2 lines elided …\n5: RUN pip install"
    assert strip_line_numbers(numbered) == "FROM python:3.10\nRUN pip install"

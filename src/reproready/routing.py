"""Versioned basename → (stage, channel) routing table — the deterministic screen.

A path is classified into at most **one** ``(stage, channel)`` cell by the
first matching rule in :data:`RULES`. Every
non-README file routes to the **Implementation** channel of its presence stage;
the README / prose carries the **Documentation** channel and is detected
separately by :func:`is_renderable_readme` (one README lights up the
Documentation cell of every stage). The table is versioned:
change it only by bumping :data:`ROUTING_VERSION`.

This is the **2-channel** routing. An earlier design split machine-actionable
files into Automation vs. Files; the current model collapses both into a single
Implementation channel ("a pinned ``Dockerfile`` and an ``install.sh`` are
equally machine-actionable"). The stage assignments are unchanged; only the
channel label is unified.

Weak execution credit: a computational source module (``model.py``,
``losses.py``, …) is the *substance being reproduced*, not a driver. It falls
through to the ``X/implementation`` catch-all as a *runnable unit* and counts
toward Execution scope, but ``1`` still requires a real orchestrator (a rubric
check).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

ROUTING_VERSION = "routing-v2"

STAGES: tuple[str, ...] = ("E", "I", "X", "V")
CHANNELS: tuple[str, ...] = ("implementation", "documentation")

IMPLEMENTATION = "implementation"
DOCUMENTATION = "documentation"


@dataclass(frozen=True)
class Cell:
    """One ``(stage, channel)`` rubric cell."""

    stage: str
    channel: str

    def __str__(self) -> str:  # "E/implementation"
        return f"{self.stage}/{self.channel}"


# --- extension / junk vocabularies ------------------------------------------

# Source files that count as **runnable units** for Execution scope and
# feed the ``X/implementation`` computational catch-all. Covers Python and the
# other languages that commonly appear in research code.
CODE_EXTS: frozenset[str] = frozenset(
    {
        ".py",
        ".ipynb",
        ".sh",
        ".r",
        ".jl",
        ".m",
        ".cpp",
        ".cc",
        ".cxx",
        ".c",
        ".cu",
        ".h",
        ".hpp",
        ".java",
        ".scala",
        ".go",
        ".rs",
        ".js",
        ".ts",
        ".lua",
        ".f",
        ".f90",
    }
)

# Declarative config files (Inputs). ``.json`` doubles as data; either way the
# cell is I/implementation, so we need not disambiguate here.
CONFIG_EXTS: frozenset[str] = frozenset({".yaml", ".yml", ".json", ".gin"})

# Concrete dataset bytes (Inputs). Existence is the signal; the extractor never
# reads these (it reports path + size). Model checkpoints are excluded — they
# are weights, not inputs.
DATA_EXTS: frozenset[str] = frozenset(
    {
        ".csv",
        ".tsv",
        ".npy",
        ".npz",
        ".parquet",
        ".jsonl",
        ".h5",
        ".hdf5",
        ".pkl",
        ".pickle",
        ".mat",
        ".arrow",
        ".feather",
        ".libsvm",
    }
)

# Output figures bundled as validation reference artifacts (Validation), but
# only when named like a result (handled in the V rule).
FIGURE_EXTS: frozenset[str] = frozenset(
    {
        ".png",
        ".pdf",
        ".eps",
        ".jpg",
        ".jpeg",
        ".svg",
    }
)

# README extensions we treat as renderable prose (the Documentation channel precondition).
# Extensionless ``README`` counts; a binary ``README.pdf`` does not.
RENDERABLE_README_EXTS: frozenset[str] = frozenset(
    {"", ".md", ".rst", ".txt", ".markdown"}
)

# Path components that mark vendored / tooling / OS cruft. Any path containing
# one (case-insensitive) is dropped before routing, scope, and extraction so a
# vendored ``requirements.txt`` or a packaged ``site-packages`` never scores.
JUNK_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".ipynb_checkpoints",
        "node_modules",
        "__macosx",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "site-packages",
        ".venv",
        "venv",
        ".eggs",
        "bower_components",
        ".idea",
        ".vscode",
    }
)
JUNK_BASENAMES: frozenset[str] = frozenset({".ds_store", "thumbs.db"})


# --- path helpers ------------------------------------------------------------


def basename(path: str) -> str:
    """POSIX basename of a (possibly dir, trailing-slash) path."""
    return path.rstrip("/").rsplit("/", 1)[-1]


def extension(name: str) -> str:
    """Lowercased final suffix incl. dot, or ``""``. ``"tar.gz"`` → ``.gz``."""
    base = name.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


def _components(path: str) -> list[str]:
    return [c.lower() for c in path.rstrip("/").split("/") if c]


def is_junk(path: str) -> bool:
    """True for vendored / tooling / OS-cruft paths and reader ``!ERROR`` rows."""
    if "!ERROR:" in path:
        return True
    comps = _components(path)
    if any(c in JUNK_DIRS for c in comps):
        return True
    return basename(path).lower() in JUNK_BASENAMES


def is_renderable_readme(path: str) -> bool:
    """True for a README whose extension we can parse as prose."""
    base = basename(path).lower()
    if not base.startswith("readme"):
        return False
    return extension(base) in RENDERABLE_README_EXTS


def is_runnable_unit(path: str, is_dir: bool) -> bool:
    """A script, notebook, or source module counted for Execution scope."""
    if is_dir or is_junk(path):
        return False
    return extension(path) in CODE_EXTS


# --- routing rules (versioned, first-match-wins) ----------------------------

# Each rule is a predicate on (basename_lower, ext, is_dir, comps) → bool, paired
# with the Cell it assigns. Order encodes precedence: specific script/manifest
# names first, the broad computational + data catch-alls last. Every cell is on
# the single Implementation channel; the stage is what each rule decides.

_Pred = Callable[[str, str, bool, frozenset[str]], bool]


def _name_re(pattern: str) -> _Pred:
    rx = re.compile(pattern)
    return lambda base, ext, is_dir, comps: (not is_dir) and bool(rx.match(base))


def _code_name_re(pattern: str) -> _Pred:
    """Match a basename pattern, but only for source-code extensions."""
    rx = re.compile(pattern)
    return lambda base, ext, is_dir, comps: (
        (not is_dir) and ext in CODE_EXTS and bool(rx.match(base))
    )


def _has_dir(*names: str) -> _Pred:
    wanted = frozenset(names)
    return lambda base, ext, is_dir, comps: bool(wanted & comps)


def _ext_in(exts: frozenset[str]) -> _Pred:
    return lambda base, ext, is_dir, comps: (not is_dir) and ext in exts


RULES: list[tuple[Cell, _Pred]] = [
    # --- Environment: dependency & container manifests ----------------------
    (Cell("E", IMPLEMENTATION), _name_re(r"^requirements.*\.txt$")),
    (Cell("E", IMPLEMENTATION), _name_re(r"^environment.*\.ya?ml$")),
    (Cell("E", IMPLEMENTATION), _name_re(r"^conda.*\.ya?ml$")),
    (Cell("E", IMPLEMENTATION), _name_re(r"^pipfile(\.lock)?$")),
    (Cell("E", IMPLEMENTATION), _name_re(r"^(pyproject\.toml|setup\.py|setup\.cfg)$")),
    (Cell("E", IMPLEMENTATION), _name_re(r"^dockerfile($|\.)")),
    (Cell("E", IMPLEMENTATION), _name_re(r"^singularity($|\.)")),
    (Cell("E", IMPLEMENTATION), _name_re(r"^(renv\.lock|description)$")),
    (Cell("E", IMPLEMENTATION), _ext_in(frozenset({".lock"}))),
    (Cell("E", IMPLEMENTATION), _has_dir(".devcontainer")),
    # --- Environment: env-provisioning scripts ------------------------------
    (
        Cell("E", IMPLEMENTATION),
        _name_re(r"^(install|setup|build|env|create_env|make_env)\.sh$"),
    ),
    # --- Inputs: data acquisition & preprocessing scripts -------------------
    (
        Cell("I", IMPLEMENTATION),
        _code_name_re(
            r"^(download|get_data|getdata|fetch|preprocess|prepare_data|prepare_dataset|make_dataset|build_dataset)"
        ),
    ),
    # --- Validation: eval / plot / analyze scripts --------------------------
    (
        Cell("V", IMPLEMENTATION),
        _code_name_re(
            r"^(eval|evaluate|evaluation|plot|analyze|analyse|analysis|visualize|visualise)"
        ),
    ),
    # --- Execution: pipeline driver scripts ---------------------------------
    (
        Cell("X", IMPLEMENTATION),
        _code_name_re(
            r"^(run_all|run_experiments|run_exp|reproduce|repro|run_pipeline|pipeline)"
        ),
    ),
    (Cell("X", IMPLEMENTATION), _name_re(r"^train\.py$")),
    # --- Validation: result producers & bundled reference outputs -----------
    (Cell("V", IMPLEMENTATION), _name_re(r"^(table|fig|figure|plot|generate_|gen_)")),
    (Cell("V", IMPLEMENTATION), _name_re(r"^expected")),
    (Cell("V", IMPLEMENTATION), _name_re(r".*results?.*\.(csv|json|txt|tsv)$")),
    (Cell("V", IMPLEMENTATION), _ext_in(frozenset({".log"}))),
    (Cell("V", IMPLEMENTATION), _has_dir("results", "outputs", "output", "logs")),
    # --- Execution: conventional entry points -------------------------------
    (
        Cell("X", IMPLEMENTATION),
        _name_re(r"^(main|run|app|cli|demo|launch|__main__)\.py$"),
    ),
    (
        Cell("X", IMPLEMENTATION),
        _name_re(r"^(makefile|gnumakefile|dvc\.yaml|dvc\.lock|snakefile)$"),
    ),
    # --- Execution: computational source modules (weak credit) --------------
    (Cell("X", IMPLEMENTATION), _ext_in(CODE_EXTS)),
    # --- Inputs: configs, data, dataset pointers ----------------------------
    (Cell("I", IMPLEMENTATION), _ext_in(CONFIG_EXTS)),
    (Cell("I", IMPLEMENTATION), _ext_in(DATA_EXTS)),
    (
        Cell("I", IMPLEMENTATION),
        _has_dir("data", "datasets", "dataset", "config", "configs"),
    ),
    (
        Cell("I", IMPLEMENTATION),
        _name_re(r"^(download|data|dataset|urls?|links?).*\.txt$"),
    ),
]


def classify(path: str, is_dir: bool = False) -> Cell | None:
    """Return the ``(stage, implementation)`` cell for ``path``, or ``None``.

    README files are intentionally **not** routed (the Documentation channel is
    handled separately by :mod:`scope`). Junk / vendored paths return ``None``.
    """
    if is_junk(path) or is_renderable_readme(path):
        return None
    base = basename(path).lower()
    ext = extension(base)
    comps = frozenset(_components(path))
    for cell, pred in RULES:
        if pred(base, ext, is_dir, comps):
            return cell
    return None

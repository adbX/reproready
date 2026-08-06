"""The grading rubric — a registry of deterministic cell detectors (spec §3).

Every ``(stage, channel)`` cell is graded ``0 / 0.5 / 1`` by a pure detector
over the inventory + the extracted evidence for that cell's files:

- **Presence (``0.5``)** = the routing match (a candidate file, or a README that
  documents the stage).
- **Implementation ``0.5 → 1``** = a deterministic strong check (spec §3.1) —
  pinning (E), obtainable-data + real-config (I), a real orchestrator (X). Each
  lives in :data:`STRONG_CHECKS` as a **named, individually swappable** function,
  and bumps :data:`RUBRIC_VERSION` when changed.
- **Documentation** detectors structurally cap at ``0.5`` (spec §2.2): a README
  that documents the stage scores ``0.5``, never ``1``.
- **Validation ``0.5 → 1``** is the one semantic judgment (spec §3.2). Here it is
  held at the deterministic floor (``0.5`` when a producer is present); the
  promote-only model call is the pluggable phase-2 detector.

Grading is **offline**: detectors read the extracted evidence, never re-open the
archive. Bytes are read once to build that evidence, then a rubric change
re-grades from it with no further archive access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import routing

RUBRIC_VERSION = "rubric-v1"

# Canonical human-readable stage names, keyed by stage letter (spec §2.1). The
# single source of truth for display labels — import this, never hand-type a
# legend/axis label, so callers can't drift from the spec. Note: I = **Inputs**
# (data acquisition / preprocessing / configs), NOT "Instructions": the
# Documentation channel of *every* stage carries instructions, so the letter is
# easy to mis-expand. Ordered E / I / X / V (the in-series stage order).
STAGE_NAME: dict[str, str] = {
    "E": "Environment",
    "I": "Inputs",
    "X": "Execution",
    "V": "Validation",
}

# README section-header keywords per stage (matched case-insensitively). Shared
# with the phase-2 Validation prompt's README slice.
STAGE_README_KEYWORDS: dict[str, tuple[str, ...]] = {
    "E": (
        "install",
        "setup",
        "requirement",
        "depend",
        "environment",
        "conda",
        "pip",
        "docker",
        "build",
        "prerequisite",
    ),
    "I": (
        "data",
        "dataset",
        "download",
        "input",
        "preprocess",
        "config",
        "hyperparam",
        "prepare",
    ),
    "X": (
        "usage",
        "run",
        "train",
        "execut",
        "quickstart",
        "command",
        "reproduce",
        "how to",
        "getting started",
        "demo",
    ),
    "V": (
        "result",
        "evaluat",
        "figure",
        "table",
        "metric",
        "benchmark",
        "accuracy",
        "performance",
        "score",
        "report",
    ),
}

# Sub-role patterns used *only inside the strong checks* (never as score
# channels — the finer Script/Spec split is not part of this version).
_ACQUISITION_RE = re.compile(
    r"^(download|get_data|getdata|fetch|preprocess|prepare_data|prepare_dataset"
    r"|make_dataset|build_dataset)"
)
_ORCH_NAME_RE = re.compile(
    r"^(run_all|run_experiments|run_exp|reproduce|repro|run_pipeline|pipeline)"
)
_ENTRY_RE = re.compile(r"^(main|run|app|cli|demo|launch|__main__|train)\.py$")
_ORCH_FILES = frozenset(
    {"makefile", "gnumakefile", "dvc.yaml", "dvc.lock", "snakefile"}
)
_POINTER_RE = re.compile(r"^(download|data|dataset|urls?|links?).*\.txt$")
_SUBPROCESS_RE = re.compile(r"subprocess\.|os\.system|check_call|check_output")
_LINE_PREFIX = re.compile(r"^\s*\d+:\s?")


@dataclass(frozen=True)
class CellGrade:
    """One graded cell: grade, the detector that fired, pointers, evidence."""

    grade: float  # 0 | 0.5 | 1
    detector_id: str
    pointers: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)


@dataclass
class GradeContext:
    """Everything the detectors read for one artifact (no archive access).

    ``text`` holds the **de-numbered** content of the extracted files (line-number
    prefixes stripped so content regexes anchor correctly).
    """

    pointers: dict[str, list[str]]  # stage → implementation-routed paths
    text: dict[str, str]  # path → de-numbered content
    sizes: dict[str, int]  # path → size (data-obtainable check)
    readme_path: str
    has_readme: bool
    readme_dark: bool
    n_runnable: int


def strip_line_numbers(numbered: str) -> str:
    """Drop the ``N: `` prefix the extractor adds, for content matching."""
    out: list[str] = []
    for ln in numbered.split("\n"):
        if "elided" in ln and "…" in ln:
            continue
        out.append(_LINE_PREFIX.sub("", ln))
    return "\n".join(out)


# --- deterministic strong checks (spec §3.1) ---------------------------------


def _env_strong(
    ctx: GradeContext, paths: list[str]
) -> tuple[bool, list[dict[str, str]]]:
    """Versions pinned: lockfile, ``==`` in requirements, ``=ver`` in conda,
    pinned Docker base (not ``:latest``), or ``==`` in pyproject/setup."""
    for p in paths:
        base = routing.basename(p).lower()
        if base.endswith(".lock") or base == "pipfile.lock":
            return True, [{"path": p, "detail": "lockfile"}]
    for p in paths:
        base = routing.basename(p).lower()
        t = ctx.text.get(p)
        if t is None:
            continue
        if base.startswith("requirements") and re.search(r"==\s*\S", t):
            return True, [{"path": p, "detail": "pinned (==) requirement"}]
        if base.startswith(("environment", "conda")) and re.search(
            r"[A-Za-z0-9_.\-]+\s*=\s*\d", t
        ):
            return True, [{"path": p, "detail": "versioned conda env"}]
        if (
            base.startswith("dockerfile")
            and re.search(r"(?im)^\s*from\s+\S+(:[\w.\-]+|@sha256)", t)
            and ":latest" not in t.lower()
        ):
            return True, [{"path": p, "detail": "pinned Docker base"}]
        if base in ("pyproject.toml", "setup.py", "setup.cfg") and "==" in t:
            return True, [{"path": p, "detail": "pinned (==) in pyproject/setup"}]
    return False, []


def _inputs_strong(
    ctx: GradeContext, paths: list[str]
) -> tuple[bool, list[dict[str, str]]]:
    """Data obtainable AND a config with real values."""
    data_paths = [
        p
        for p in paths
        if routing.extension(routing.basename(p).lower()) in routing.DATA_EXTS
    ]
    data_bytes = sum(max(ctx.sizes.get(p, 0), 0) for p in data_paths)
    has_dl = any(_ACQUISITION_RE.match(routing.basename(p).lower()) for p in paths)
    ptr_url = any(
        "http" in (ctx.text.get(p, "").lower())
        for p in paths
        if _POINTER_RE.match(routing.basename(p).lower())
    )
    data_obt = bool(has_dl or ptr_url or data_bytes > 50_000 or len(data_paths) >= 3)

    cfg_real = False
    cfg_path = ""
    for p in paths:
        if routing.extension(routing.basename(p).lower()) not in routing.CONFIG_EXTS:
            continue
        t = ctx.text.get(p)
        if t is None:
            continue
        t = t.strip()
        body = [
            ln
            for ln in t.splitlines()
            if ln.strip() and not ln.strip().startswith(("#", "//"))
        ]
        if len(t) > 64 and len(body) >= 2 and (":" in t or "=" in t):
            cfg_real, cfg_path = True, p
            break

    strong = bool(data_obt and cfg_real)
    ev: list[dict[str, str]] = []
    if strong:
        ev.append({"path": cfg_path, "detail": "real config + obtainable data"})
    return strong, ev


def _exec_strong(
    ctx: GradeContext, paths: list[str]
) -> tuple[bool, list[dict[str, str]]]:
    """A real orchestrator: run_all/Makefile/dvc/snakefile, or an entry point
    that shells out (``subprocess``/``os.system``/``check_call``)."""
    for p in paths:
        base = routing.basename(p).lower()
        if base in _ORCH_FILES:
            return True, [{"path": p, "detail": "orchestrator file"}]
    for p in paths:
        base = routing.basename(p).lower()
        if _ORCH_NAME_RE.match(base) and routing.extension(base) in routing.CODE_EXTS:
            return True, [{"path": p, "detail": "pipeline driver script"}]
    for p in paths:
        base = routing.basename(p).lower()
        if not _ENTRY_RE.match(base):
            continue
        t = ctx.text.get(p)
        if t and _SUBPROCESS_RE.search(t):
            return True, [{"path": p, "detail": "entry point shells out"}]
    return False, []


# The strong-check registry. Swap an entry (and bump RUBRIC_VERSION) to change
# the Implementation ``0.5 → 1`` promotion. Validation has no entry: its
# 0.5 → 1 promotion is the phase-2 model detector, not a deterministic check.
STRONG_CHECKS = {
    "E": _env_strong,
    "I": _inputs_strong,
    "X": _exec_strong,
}


# --- README section detection (Documentation channel) ------------------------


def _readme_documents_stage(text: str, stage: str) -> bool:
    """True iff the README has a section / prose documenting ``stage`` (§3.1).

    Markdown headers (``#``) bound sections — a stage is documented iff a
    matching header has non-empty body. README forms without headers
    (``.rst`` / ``.txt``) fall back to a keyword match anywhere in the prose.
    """
    if not text.strip():
        return False
    keywords = STAGE_README_KEYWORDS[stage]
    lines = text.split("\n")
    sections: list[tuple[bool, list[str]]] = []
    intro: list[str] = []
    cur: tuple[bool, list[str]] | None = None
    for ln in lines:
        if ln.lstrip().startswith("#"):
            match = any(k in ln.lower() for k in keywords)
            cur = (match, [])
            sections.append(cur)
        elif cur is None:
            intro.append(ln)
        else:
            cur[1].append(ln)

    if not sections:  # no markdown headers → keyword anywhere
        return any(k in text.lower() for k in keywords)

    for match, body in sections:
        if match and any(b.strip() for b in body):
            return True
    # A keyword in the intro (before the first header) also counts as documented.
    return any(k in " ".join(intro).lower() for k in keywords)


# --- the detectors -----------------------------------------------------------


def _grade_implementation(stage: str, ctx: GradeContext) -> CellGrade:
    paths = ctx.pointers.get(stage, [])
    if not paths:
        return CellGrade(0.0, f"{stage}/implementation/absent")
    check = STRONG_CHECKS.get(stage)
    if check is not None:
        strong, ev = check(ctx, paths)
        if strong:
            return CellGrade(1.0, f"{stage}/implementation/strong", paths, ev)
    return CellGrade(0.5, f"{stage}/implementation/present", paths)


def _grade_documentation(stage: str, ctx: GradeContext) -> CellGrade:
    if not ctx.has_readme or ctx.readme_dark:
        return CellGrade(0.0, f"{stage}/documentation/absent")
    text = ctx.text.get(ctx.readme_path, "")
    if _readme_documents_stage(text, stage):
        return CellGrade(
            0.5,
            f"{stage}/documentation/present",
            [ctx.readme_path],
            [{"path": ctx.readme_path, "detail": f"README documents {stage}"}],
        )
    return CellGrade(0.0, f"{stage}/documentation/absent")


def grade_cell(cell: routing.Cell, ctx: GradeContext) -> CellGrade:
    """Grade one ``(stage, channel)`` cell deterministically."""
    if cell.channel == routing.DOCUMENTATION:
        return _grade_documentation(cell.stage, ctx)
    return _grade_implementation(cell.stage, ctx)


def grade_all(ctx: GradeContext) -> dict[routing.Cell, CellGrade]:
    """Grade the full 4 × 2 grid (Validation/Implementation held at its floor)."""
    out: dict[routing.Cell, CellGrade] = {}
    for stage in routing.STAGES:
        for channel in routing.CHANNELS:
            cell = routing.Cell(stage, channel)
            out[cell] = grade_cell(cell, ctx)
    return out

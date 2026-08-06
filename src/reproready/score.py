"""The store-free single-artifact scoring pipeline.

Takes one code artifact — a directory, ``.zip``, or ``.tar.gz`` — and runs the
whole pipeline in memory, returning an :class:`ArtifactReport`:

    inventory → junk filter → route → scope → targeted byte reads →
    evidence extraction → deterministic rubric → aggregate

No persistence, no database, no drive layout. Everything the report needs is
computed from the artifact's own bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import aggregate as agg
from . import content, extract, routing, rubric
from . import scope as scope_mod
from .inventory import RawEntry, read_archive, read_dir

# Per-cell evidence-extraction cap — enough content for the strong checks.
EVIDENCE_FILES_PER_CELL = 12


@dataclass(frozen=True)
class ArtifactReport:
    """One artifact's full readiness report."""

    path: str
    archive_kind: str  # zip / targz / gz / pdf / dir / none
    has_code: bool
    in_scope: dict[str, bool]  # stage → in-scope flag
    n_runnable: int
    readme_path: str
    has_readme: bool
    readme_dark: bool
    pointers: dict[str, list[str]]  # stage → routed implementation pointers
    grid: dict[routing.Cell, rubric.CellGrade]  # 4 × 2 graded cell grid
    evidence: dict[str, extract.Extracted]  # path → extracted evidence
    vector: dict[str, float]  # scored stage → R_s
    r: float | None  # ReproReady score (None when no code)
    tier: str  # 'N/A' | '0' | '1' | '2' | '3'
    coverage: int  # in-scope stages visible to a static read
    redundant: dict[str, bool]  # scored stage → both channels ≥ 0.5

    @property
    def cells_flat(self) -> dict[tuple[str, str], float]:
        """``{(stage, channel): grade}`` — the flat grid the aggregator reads."""
        return {(c.stage, c.channel): g.grade for c, g in self.grid.items()}


# ---------------------------------------------------------------------------
# routing → pointers + evidence targets
# ---------------------------------------------------------------------------


def _route(entries: list[RawEntry]) -> dict[str, list[str]]:
    """Routed Implementation pointers per stage, sorted (depth, path) for determinism."""
    by_stage: dict[str, list[tuple[int, str]]] = {}
    for e in entries:
        if routing.is_junk(e.path):
            continue
        cell = routing.classify(e.path, e.is_dir)
        if cell is not None:
            by_stage.setdefault(cell.stage, []).append((e.depth, e.path))
    return {s: [p for _, p in sorted(v)] for s, v in by_stage.items()}


def _evidence_targets(pointers: dict[str, list[str]], sc: scope_mod.Scope) -> set[str]:
    """Files whose bytes the rubric needs: capped code pointers + README."""
    targets: set[str] = set()
    for paths in pointers.values():
        ordered = sorted(paths, key=lambda p: (p.count("/"), p))
        targets.update(ordered[:EVIDENCE_FILES_PER_CELL])
    if sc.has_readme and not sc.readme_dark:
        targets.add(sc.readme_path)
    return targets


def _read_evidence(
    path: Path,
    ext_hint: str,
    targets: set[str],
    sizes: dict[str, int],
) -> dict[str, extract.Extracted]:
    """Read the target members' bytes and summarise each into citable evidence."""
    # Skip pulling bytes for large data files — existence is the signal.
    to_read = {
        t
        for t in targets
        if not (
            extract.kind_for(t) == "data_stat"
            and sizes.get(t, 0) > extract.DATA_HEADER_MAX_BYTES
        )
    }
    blobs = content.read_members(path, ext_hint, to_read)
    evidence: dict[str, extract.Extracted] = {}
    for t in sorted(targets):
        data = blobs.get(t)
        size = sizes.get(t, -1)
        evidence[t] = extract.extract_evidence(t, data, size)
    return evidence


def _build_context(
    pointers: dict[str, list[str]],
    sc: scope_mod.Scope,
    sizes: dict[str, int],
    evidence: dict[str, extract.Extracted],
) -> rubric.GradeContext:
    text = {p: rubric.strip_line_numbers(ev.content) for p, ev in evidence.items()}
    return rubric.GradeContext(
        pointers=pointers,
        text=text,
        sizes=sizes,
        readme_path=sc.readme_path,
        has_readme=sc.has_readme,
        readme_dark=bool(sc.readme_dark),
        n_runnable=sc.n_runnable,
    )


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


_EXT_HINTS: tuple[str, ...] = (".tar.gz", ".tgz", ".zip", ".gz", ".pdf")


def infer_ext_hint(path: Path) -> str:
    """Best-effort extension hint from a filename (``""`` when unrecognised)."""
    name = path.name.lower()
    for suf in _EXT_HINTS:
        if name.endswith(suf):
            return suf
    return ""


def _read_inventory(path: Path, ext_hint: str | None):
    if path.is_dir():
        return read_dir(path), ""
    hint = ext_hint if ext_hint is not None else infer_ext_hint(path)
    return read_archive(path, hint), hint


def score_path(
    path: str | Path,
    ext_hint: str | None = None,
    config: agg.AggregationConfig | None = None,
) -> ArtifactReport:
    """Score one artifact (a directory, ``.zip``, or ``.tar.gz``) into a report.

    ``ext_hint`` overrides the extension inferred from the filename (ignored for
    a directory). ``config`` selects the aggregation variant; it defaults to the
    canonical mapping (:data:`~reproready.aggregate.PROMOTED_CONFIG`), which
    re-bands the tiers and scopes out false zeros.
    """
    path = Path(path)
    cfg = config if config is not None else agg.PROMOTED_CONFIG

    result, hint = _read_inventory(path, ext_hint)
    entries = result.entries
    sizes = {e.path: e.size for e in entries}

    has_code = any(routing.is_runnable_unit(e.path, e.is_dir) for e in entries)
    pointers = _route(entries)
    sc = scope_mod.compute_scope(entries)
    targets = _evidence_targets(pointers, sc)
    evidence = _read_evidence(path, hint, targets, sizes)

    ctx = _build_context(pointers, sc, sizes, evidence)
    grid = rubric.grade_all(ctx)
    cells_flat = {(cell.stage, cell.channel): cg.grade for cell, cg in grid.items()}

    out = agg.aggregate(
        cells_flat,
        sc.in_scope,
        has_code,
        bool(sc.readme_dark),
        cfg,
        has_readme=bool(sc.has_readme),
    )
    return ArtifactReport(
        path=str(path),
        archive_kind=result.archive_kind,
        has_code=has_code,
        in_scope=dict(sc.in_scope),
        n_runnable=sc.n_runnable,
        readme_path=sc.readme_path,
        has_readme=sc.has_readme,
        readme_dark=bool(sc.readme_dark),
        pointers=pointers,
        grid=grid,
        evidence=evidence,
        vector=out.vector,
        r=out.r if has_code else None,
        tier=out.tier,
        coverage=out.coverage,
        redundant=out.redundant,
    )

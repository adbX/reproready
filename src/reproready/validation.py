"""Phase-2 Validation — build the manuscript-free context, drive the single
promote-only model call, apply the verdict, and re-aggregate.

The pure helpers here (:func:`readme_results_slice`, :func:`producer_snippet`)
operate on plain strings, so they can be reused by a store-backed pipeline as
well as the in-memory :class:`~reproready.score.ArtifactReport` flow.

A verdict is applied **promote-only**: a ``promote=True`` lifts
``V/implementation`` ``0.5 → 1``; a ``promote=False`` / malformed / ``None``
holds the deterministic floor and never lowers it. See spec §3.2.
"""

from __future__ import annotations

from dataclasses import replace

from . import aggregate as agg
from . import prompts, routing, rubric
from .prompts import VALIDATION_MODEL, ValidationContext, Verdict
from .score import ArtifactReport

# Detector ids that record the model's outcome on V/implementation. The
# deterministic floor leaves V/implementation at ``V/implementation/present``; a
# call rewrites it to one of these (the distinguisher between "called, kept" and
# "not yet called").
FLOOR_DETECTOR = "V/implementation/present"
PROMOTED_DETECTOR = "V/implementation/model/promoted"
KEPT_DETECTOR = "V/implementation/model/kept"

# Keep the call small: cap producers shown and the per-snippet / README slice.
PRODUCERS_CAP = 12
SNIPPET_MAX_CHARS = 1000
README_SLICE_MAX_CHARS = 1500


# ---------------------------------------------------------------------------
# pure context helpers (string in, string out)
# ---------------------------------------------------------------------------


def readme_results_slice(text: str, max_chars: int = README_SLICE_MAX_CHARS) -> str:
    """Extract the README's results/evaluation section — the V Documentation slice.

    Mirrors :func:`rubric._readme_documents_stage`'s section logic (Markdown
    ``#`` headers bound sections; a stage is documented by a matching header with
    a non-empty body, or a keyword in the intro / header-less prose) but returns
    the matching section text instead of a boolean.
    """
    if not text.strip():
        return ""
    keywords = rubric.STAGE_README_KEYWORDS["V"]
    sections: list[tuple[str, bool, list[str]]] = []  # (header, match, body)
    intro: list[str] = []
    cur: tuple[str, bool, list[str]] | None = None
    for ln in text.split("\n"):
        if ln.lstrip().startswith("#"):
            cur = (ln, any(k in ln.lower() for k in keywords), [])
            sections.append(cur)
        elif cur is None:
            intro.append(ln)
        else:
            cur[2].append(ln)

    out: list[str] = []
    if not sections:  # no headers → keyword anywhere: take the whole prose
        if any(k in text.lower() for k in keywords):
            out.append(text.strip())
    else:
        for header, match, body in sections:
            if match and any(b.strip() for b in body):
                out.append("\n".join([header, *body]).strip())
        if not out and any(k in " ".join(intro).lower() for k in keywords):
            out.append("\n".join(intro).strip())
    return "\n\n".join(s for s in out if s)[:max_chars]


def producer_snippet(content: str | None) -> str:
    """The output-writing call-sites for one producer, de-numbered + capped."""
    if not content:
        return ""
    return rubric.strip_line_numbers(content)[:SNIPPET_MAX_CHARS]


# ---------------------------------------------------------------------------
# in-memory report flow
# ---------------------------------------------------------------------------


def is_validation_candidate(report: ArtifactReport) -> bool:
    """``V`` in scope and a non-empty ``V/implementation`` pointer list (producers)."""
    return bool(report.in_scope.get("V", False) and report.pointers.get("V"))


def build_context(report: ArtifactReport) -> ValidationContext:
    """Build the manuscript-free context from a scored report."""
    pointers = report.pointers.get("V", [])
    producers = [
        (routing.basename(p), producer_snippet(_evidence_content(report, p)))
        for p in pointers[:PRODUCERS_CAP]
    ]
    readme_results = ""
    if report.has_readme and not report.readme_dark and report.readme_path:
        rev = report.evidence.get(report.readme_path)
        if rev is not None:
            readme_results = readme_results_slice(
                rubric.strip_line_numbers(rev.content)
            )
    return ValidationContext(producers=producers, readme_results=readme_results)


def _evidence_content(report: ArtifactReport, path: str) -> str | None:
    ev = report.evidence.get(path)
    return ev.content if ev is not None else None


def apply_verdict(
    report: ArtifactReport,
    verdict: Verdict | None,
    config: agg.AggregationConfig | None = None,
) -> ArtifactReport:
    """Apply a verdict **promote-only** and return a re-aggregated report.

    - ``promote=True`` → ``V/implementation`` grade ``→ 1.0``, detector
      ``…/model/promoted``, evidence records ``{result_ref, confidence}``.
    - ``promote=False`` / malformed / ``None`` → grade **stays 0.5** (the
      deterministic floor; never lowered), detector ``…/model/kept``.
    """
    cfg = config if config is not None else agg.PROMOTED_CONFIG
    promote = verdict is not None and verdict.promote
    v_cell = routing.Cell("V", routing.IMPLEMENTATION)
    old = report.grid.get(v_cell)

    new_grid = dict(report.grid)
    if old is not None and old.grade > 0.0:  # a producer is present (floor 0.5)
        if promote:
            new_grid[v_cell] = rubric.CellGrade(
                1.0,
                PROMOTED_DETECTOR,
                old.pointers,
                [{"result_ref": verdict.result_ref, "confidence": verdict.confidence}],
            )
        else:
            new_grid[v_cell] = rubric.CellGrade(
                0.5,
                KEPT_DETECTOR,
                old.pointers,
                old.evidence,
            )

    cells_flat = {(c.stage, c.channel): g.grade for c, g in new_grid.items()}
    out = agg.aggregate(
        cells_flat,
        report.in_scope,
        report.has_code,
        report.readme_dark,
        cfg,
        has_readme=report.has_readme,
    )
    return replace(
        report,
        grid=new_grid,
        vector=out.vector,
        r=out.r if report.has_code else None,
        tier=out.tier,
        coverage=out.coverage,
        redundant=out.redundant,
    )


def validate_report(
    report: ArtifactReport,
    model: str = VALIDATION_MODEL,
    client=None,
    config: agg.AggregationConfig | None = None,
) -> tuple[ArtifactReport, Verdict | None]:
    """Run the single promote-only model call and apply its verdict.

    ``anthropic`` is imported lazily (the ``llm`` extra) and only when ``client``
    is not supplied. A non-candidate report is returned unchanged with a ``None``
    verdict. Requires ``ANTHROPIC_API_KEY`` in the environment.
    """
    if not is_validation_candidate(report):
        return report, None
    ctx = build_context(report)
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    msg = client.messages.create(**prompts.build_request_kwargs(ctx, model))
    verdict = prompts.parse_verdict(prompts.extract_text(msg))
    return apply_verdict(report, verdict, config), verdict

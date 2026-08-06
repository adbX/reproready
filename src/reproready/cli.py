"""Command-line entry point: ``reproready score PATH...``.

Scores one or more code artifacts (a directory, ``.zip``, or ``.tar.gz``) and
renders a readiness report, or emits JSON with ``--json``. ``--validate`` runs
the single promote-only model call (needs ``ANTHROPIC_API_KEY`` and the ``llm``
extra: ``pip install 'reproready[llm]'``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import prompts, routing
from .score import ArtifactReport, score_path

console = Console()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reproready",
        description="Static reproduction-readiness scoring for code artifacts.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    score = sub.add_parser("score", help="Score a directory / .zip / .tar.gz artifact.")
    score.add_argument(
        "paths",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="Artifact path(s): a directory, .zip, or .tar.gz.",
    )
    score.add_argument(
        "--json", action="store_true", help="Emit JSON instead of a rendered report."
    )
    score.add_argument(
        "--validate",
        action="store_true",
        help="Run the promote-only Validation model call (needs ANTHROPIC_API_KEY).",
    )
    score.add_argument(
        "--model",
        default=prompts.VALIDATION_MODEL,
        help=f"Model for --validate (default: {prompts.VALIDATION_MODEL}).",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------


def report_to_dict(report: ArtifactReport) -> dict:
    """A JSON-serialisable view of a report (evidence reduced to a summary)."""
    grid = [
        {
            "stage": cell.stage,
            "channel": cell.channel,
            "grade": cg.grade,
            "detector_id": cg.detector_id,
            "pointers": cg.pointers,
            "evidence": cg.evidence,
        }
        for cell, cg in sorted(
            report.grid.items(), key=lambda kv: (kv[0].stage, kv[0].channel)
        )
    ]
    evidence = [
        {
            "path": path,
            "kind": ev.kind,
            "truncated": ev.truncated,
            "n_lines_orig": ev.n_lines_orig,
        }
        for path, ev in sorted(report.evidence.items())
    ]
    return {
        "path": report.path,
        "archive_kind": report.archive_kind,
        "has_code": report.has_code,
        "in_scope": report.in_scope,
        "n_runnable": report.n_runnable,
        "readme_path": report.readme_path,
        "has_readme": report.has_readme,
        "readme_dark": report.readme_dark,
        "pointers": report.pointers,
        "grid": grid,
        "evidence": evidence,
        "vector": report.vector,
        "r": report.r,
        "tier": report.tier,
        "coverage": report.coverage,
        "redundant": report.redundant,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _fmt(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "—"


def render_report(report: ArtifactReport) -> None:
    scope = report.in_scope
    n_scope = sum(1 for v in scope.values() if v)
    summary = [
        f"[bold]{report.path}[/]  kind={report.archive_kind}  has_code={report.has_code}",
        f"scope E/I/X/V = {int(scope['E'])}/{int(scope['I'])}/{int(scope['X'])}/{int(scope['V'])}  "
        f"runnable={report.n_runnable}  readme={report.readme_path or '—'}"
        f"{' (dark)' if report.readme_dark else ''}",
        f"[bold]R={_fmt(report.r)}[/]  tier={report.tier}  coverage={report.coverage}/{n_scope}",
        "stage R  "
        + "  ".join(f"{s}={_fmt(report.vector.get(s))}" for s in routing.STAGES),
    ]
    console.print(Panel("\n".join(summary), title="ReproReady", expand=False))

    table = Table(title="cells", show_header=True)
    for col in ("stage", "chan", "scope", "grade", "detector", "pointers"):
        table.add_column(col)
    for cell, cg in sorted(
        report.grid.items(), key=lambda kv: (kv[0].stage, kv[0].channel)
    ):
        pts = cg.pointers
        ptxt = ", ".join(pts[:3]) + (f" +{len(pts) - 3}" if len(pts) > 3 else "")
        table.add_row(
            cell.stage,
            cell.channel,
            str(int(scope.get(cell.stage, False))),
            _fmt(cg.grade),
            cg.detector_id or "—",
            ptxt or "—",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _score_one(path: Path, validate: bool, model: str) -> ArtifactReport:
    report = score_path(path)
    if validate:
        from .validation import validate_report

        report, _ = validate_report(report, model=model)
    return report


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    reports: list[ArtifactReport] = []
    for path in args.paths:
        if not path.exists():
            console.print(f"[red]no such path: {path}[/]", highlight=False)
            raise SystemExit(2)
        reports.append(_score_one(path, args.validate, args.model))

    if args.json:
        payload = [report_to_dict(r) for r in reports]
        json.dump(payload if len(payload) > 1 else payload[0], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    for report in reports:
        render_report(report)


if __name__ == "__main__":
    main()

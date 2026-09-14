"""Command-line entry points for ReproReady scoring and static checking."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import prompts, routing
from .checker import check_path
from .checker_render import render_report as render_check_report
from .checker_render import report_console
from .checker_types import CheckInputError
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
    check = sub.add_parser("check", help="Statically inspect one regular file.")
    check.add_argument(
        "path",
        type=Path,
        metavar="PATH",
        help="One Python file, notebook, ZIP, or other regular file.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="Emit the complete checker JSON report instead of the terminal summary.",
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


def _run_check(path: Path, as_json: bool) -> int:
    try:
        report = check_path(path)
        if as_json:
            json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            render_check_report(report.to_dict(), report_console())
    except CheckInputError as error:
        print(
            f"reproready check: error: {error.message} ({error.code})",
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            "reproready check: error: An internal failure prevented a checker "
            "report. (internal_error)",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "check":
        return _run_check(args.path, args.json)

    reports: list[ArtifactReport] = []
    for path in args.paths:
        if not path.exists():
            console.print(f"[red]no such path: {path}[/]", highlight=False)
            return 2
        reports.append(_score_one(path, args.validate, args.model))

    if args.json:
        payload = [report_to_dict(r) for r in reports]
        json.dump(payload if len(payload) > 1 else payload[0], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    for report in reports:
        render_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

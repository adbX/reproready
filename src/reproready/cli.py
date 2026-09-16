"""Command-line entry points for ReproReady scoring and static checking."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import prompts, routing, rubric
from .checker import check_path
from .checker_render import _terminal_text, report_console
from .checker_render import render_report as render_check_report
from .checker_report import encode_report
from .checker_types import CheckInputError
from .checker_view import SavedReportError, load_saved_report
from .score import ArtifactReport, score_path

console = Console()
error_console = Console(stderr=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    formatter = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(
        prog="reproready",
        description=(
            "Inspect artifacts without running their code, or calculate the separate "
            "ReproReady score."
        ),
        epilog="""Examples:
  reproready check artifact.zip
  reproready check artifact.zip --json > report.json
  reproready view report.json
  reproready score artifact.zip""",
        formatter_class=formatter,
    )
    subcommands = parser.add_subparsers(dest="cmd", required=True)

    check = subcommands.add_parser(
        "check",
        help="Report exact static observations for one regular file.",
        description=(
            "Inspect one supported regular file without executing artifact code. "
            "This command reports observations and incomplete work; it does not "
            "calculate the ReproReady score."
        ),
        epilog="""Examples:
  reproready check analysis.py
  reproready check artifact.zip
  reproready check artifact.zip --json > report.json""",
        formatter_class=formatter,
    )
    check.add_argument(
        "path",
        type=Path,
        metavar="INPUT",
        help="Python file, notebook, dependency file, ZIP, or other regular file.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="Write the complete compact checker JSON document to stdout.",
    )

    view = subcommands.add_parser(
        "view",
        help="Display one saved checker JSON report.",
        description=(
            "Validate and display a saved checker v1 report without reopening or "
            "inspecting the original artifact."
        ),
        epilog="""Examples:
  reproready view report.json
  reproready view report.json --all""",
        formatter_class=formatter,
    )
    view.add_argument(
        "path",
        type=Path,
        metavar="REPORT.json",
        help="Saved ReproReady checker v1 report.",
    )
    view.add_argument(
        "--all",
        action="store_true",
        help="Display every retained location and useful linked evidence.",
    )

    score = subcommands.add_parser(
        "score",
        help="Calculate the ReproReady score for one or more artifacts.",
        description=(
            "Measure static reproduction readiness for directories, ZIPs, and "
            "tar-gzip archives. Scoring is separate from checker observations and "
            "does not execute artifact code."
        ),
        epilog="""Examples:
  reproready score artifact/
  reproready score artifact.zip --json
  reproready score first.zip second.tar.gz""",
        formatter_class=formatter,
    )
    score.add_argument(
        "paths",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="Artifact directory, ZIP, or tar-gzip path.",
    )
    score.add_argument(
        "--json", action="store_true", help="Write score JSON instead of tables."
    )
    score.add_argument(
        "--validate",
        action="store_true",
        help="Run promote-only Validation (requires ANTHROPIC_API_KEY).",
    )
    score.add_argument(
        "--model",
        default=prompts.VALIDATION_MODEL,
        help=f"Model for --validate (default: {prompts.VALIDATION_MODEL}).",
    )
    return parser.parse_args(argv)


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
            report.grid.items(), key=lambda item: (item[0].stage, item[0].channel)
        )
    ]
    evidence = [
        {
            "path": path,
            "kind": item.kind,
            "truncated": item.truncated,
            "n_lines_orig": item.n_lines_orig,
        }
        for path, item in sorted(report.evidence.items())
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


_SCORE_KIND = {
    "dir": "Directory",
    "zip": "ZIP archive",
    "targz": "tar-gzip archive",
    "gz": "gzip file",
    "pdf": "PDF",
    "none": "Regular file",
}


def _fmt_score(value: float | None, *, unavailable: str = "Unavailable") -> str:
    return f"{value:.2f}" if value is not None else unavailable


def _score_identity(report: ArtifactReport) -> Panel:
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column(ratio=1, overflow="fold")
    details.add_row("Path", _terminal_text(report.path))
    details.add_row("Format", _SCORE_KIND.get(report.archive_kind, report.archive_kind))
    details.add_row("Code found", "Yes" if report.has_code else "No")
    details.add_row("Runnable code units", str(report.n_runnable))
    if report.has_readme:
        readme = _terminal_text(report.readme_path)
        if report.readme_dark:
            readme.append(" (content unavailable)")
    else:
        readme = Text("Not found")
    details.add_row("README", readme)
    return Panel(
        details,
        title="Artifact",
        title_align="left",
        border_style="#4f7890",
        expand=True,
    )


def _score_summary(report: ArtifactReport) -> Panel:
    in_scope = sum(report.in_scope.values())
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("Score", _fmt_score(report.r, unavailable="N/A"))
    details.add_row("Tier", report.tier)
    details.add_row(
        "Observable coverage",
        f"{report.coverage} of {in_scope} in-scope stages",
    )
    return Panel(
        details,
        title="ReproReady score",
        title_align="left",
        border_style="#6e4b70",
        expand=False,
    )


def _pointer_text(pointers: list[str]) -> Text:
    if not pointers:
        return Text("—")
    line = Text()
    for position, pointer in enumerate(pointers[:3]):
        if position:
            line.append("\n")
        line.append_text(_terminal_text(pointer))
    if len(pointers) > 3:
        line.append(f"\n+{len(pointers) - 3} more")
    return line


def render_report(report: ArtifactReport) -> None:
    """Render one score report without changing score semantics."""

    console.print(_score_identity(report))
    console.print(_score_summary(report))

    grades = {
        (cell.stage, cell.channel): grade.grade for cell, grade in report.grid.items()
    }
    stage_rows: list[tuple[str, str, str, str, Text]] = []
    for stage in routing.STAGES:
        in_scope = report.in_scope.get(stage, False)
        stage_rows.append(
            (
                rubric.STAGE_NAME[stage],
                _fmt_score(report.vector.get(stage)) if in_scope else "Out of scope",
                _fmt_score(grades.get((stage, routing.IMPLEMENTATION)))
                if in_scope
                else "—",
                _fmt_score(grades.get((stage, routing.DOCUMENTATION)))
                if in_scope
                else "—",
                _pointer_text(report.pointers.get(stage, [])),
            )
        )

    if console.width < 72:
        details = Table.grid(padding=(0, 2))
        details.add_column(style="bold")
        details.add_column(ratio=1, overflow="fold")
        for position, (
            stage,
            result,
            implementation,
            documentation,
            evidence,
        ) in enumerate(stage_rows):
            if position:
                details.add_row("", "")
            details.add_row(Text(stage, style="bold"), "")
            details.add_row("Result", result)
            details.add_row("Implementation", implementation)
            details.add_row("Documentation", documentation)
            details.add_row("Evidence", evidence)
        console.print(
            Panel(
                details,
                title="Stage evidence",
                title_align="left",
                border_style="#6e4b70",
            )
        )
        return

    table = Table(title="Stage evidence", expand=True)
    table.add_column("Stage", style="bold", no_wrap=True)
    table.add_column("Result", justify="right", no_wrap=True)
    table.add_column("Channel grades", overflow="fold")
    table.add_column("Evidence", ratio=2, overflow="fold")
    for stage, result, implementation, documentation, evidence in stage_rows:
        channels = Text()
        if implementation == documentation == "—":
            channels.append("—")
        else:
            channels.append("Implementation ", style="bold")
            channels.append(implementation)
            channels.append("\nDocumentation ", style="bold")
            channels.append(documentation)
        table.add_row(stage, result, channels, evidence)
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
            payload = encode_report(report.to_dict()) + b"\n"
            output = getattr(sys.stdout, "buffer", None)
            if output is None:
                sys.stdout.write(payload.decode("ascii"))
            else:
                output.write(payload)
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


def _run_view(path: Path, show_all: bool) -> int:
    try:
        report = load_saved_report(path)
        render_check_report(
            report,
            report_console(),
            report_path=path,
            show_all=show_all,
        )
    except SavedReportError as error:
        print(
            f"reproready view: error: {error.message} ({error.code})",
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(
            "reproready view: error: An internal failure prevented the saved report "
            "from being displayed. (internal_error)",
            file=sys.stderr,
        )
        return 1
    return 0


def _score_path_error(path: Path) -> None:
    message = Text("reproready score: error: path does not exist: ", style="red")
    message.append_text(_terminal_text(path, "red"))
    error_console.print(message)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "check":
        return _run_check(args.path, args.json)
    if args.cmd == "view":
        return _run_view(args.path, args.all)

    reports: list[ArtifactReport] = []
    for path in args.paths:
        if not path.exists():
            _score_path_error(path)
            return 2
        reports.append(_score_one(path, args.validate, args.model))

    if args.json:
        payload = [report_to_dict(report) for report in reports]
        json.dump(payload if len(payload) > 1 else payload[0], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    for report in reports:
        render_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Terminal contracts for the internal checker report renderer."""

from __future__ import annotations

import zipfile
from io import StringIO
from pathlib import Path

from rich.console import Console

from reproready.checker import intake_report
from reproready.checker_render import render_report, report_console


def _render(report: dict[str, object]) -> str:
    output = StringIO()
    console = Console(
        file=output,
        color_system=None,
        force_terminal=False,
        no_color=True,
        width=160,
    )
    render_report(report, console)
    return output.getvalue()


def test_renderer_preserves_literal_markup_and_escapes_terminal_controls(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "report[blue]\u0007\u202e.zip"
    hostile_name = "/[bold]unsafe[/bold]\u0007\u202e.py"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(hostile_name, b"value = 1\n")

    report = intake_report(archive_path)
    output = _render(report)

    assert report["artifact"]["display_name"] == "report[blue]\u0007\u202e.zip"
    assert report["rule_results"][0]["observations"][0]["snippet"] == hostile_name
    assert "Artifact: report[blue]\\u0007\\u202e.zip" in output
    assert "Kind: zip" in output
    assert "archive.structure: complete" in output
    assert "absolute_member_path [member:0]" in output
    assert "control_character_in_name [member:0]" in output
    assert "[bold]unsafe[/bold]\\u0007\\u202e.py" in output
    assert "\u0007" not in output
    assert "\u202e" not in output


def test_partial_coverage_and_reached_limits_are_legible_without_color(
    checker_inputs,
) -> None:
    report = intake_report(checker_inputs.paths["hostile_zip"])
    output = _render(report)

    assert "archive.structure: partial" in output
    assert "resource_limit_reached [member:21]" in output
    assert "Reached limit: max_nested_zip_depth" in output
    assert "Skipped: nested_zip_depth_limit [member:21]" in output
    assert "no finding in the checks run" not in output


def test_complete_empty_result_alone_receives_no_finding_phrase(
    checker_inputs,
) -> None:
    complete = _render(intake_report(checker_inputs.paths["zip64"]))
    partial = _render(intake_report(checker_inputs.paths["encrypted_zip"]))
    direct = _render(intake_report(checker_inputs.paths["minimal_python"]))

    assert "no finding in the checks run" in complete
    assert "no finding in the checks run" not in partial
    assert "no finding in the checks run" not in direct
    assert "passed" not in complete.casefold()


def test_internal_console_honors_no_color(monkeypatch, checker_inputs) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    output = StringIO()
    console = report_console(file=output)

    render_report(intake_report(checker_inputs.paths["zip64"]), console)

    assert console.no_color is True
    assert "no finding in the checks run" in output.getvalue()


def test_failed_archive_input_is_labelled_without_color(checker_inputs) -> None:
    output = _render(intake_report(checker_inputs.paths["opaque_corrupt_zip"]))

    assert "archive.structure: partial" in output
    assert "integrity_error [member:1]" in output
    assert "Failed: integrity_error [member:1]" in output

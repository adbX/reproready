"""Terminal contracts for the topic-first checker report."""

from __future__ import annotations

import copy
import zipfile
from io import StringIO
from pathlib import Path

from rich.console import Console

from reproready.checker import _check_document
from reproready.checker_render import render_report, report_console


def _render(report: dict[str, object], *, width: int = 100) -> str:
    output = StringIO()
    console = Console(
        file=output,
        color_system=None,
        force_terminal=False,
        no_color=True,
        width=width,
    )
    render_report(report, console)
    return output.getvalue()


def test_evidence_heavy_report_leads_with_topics_and_bounds_examples(
    checker_inputs,
) -> None:
    report = _check_document(checker_inputs.paths["evidence_heavy_zip"])
    output = _render(report)
    dependencies = next(
        result
        for result in report["rule_results"]
        if result["rule_id"] == "python.dependencies"
    )

    assert len(dependencies["observations"]) == 36
    assert len(dependencies["evidence"]) > 43
    assert "evidence-heavy.zip" in output
    assert "0 findings" in output
    assert "36 human-review observations" in output
    assert "1 human-review topic" in output
    assert "11 rules total: 10 complete · 1 not applicable" in output
    assert "Dependency declarations without exact imports" in output
    assert "project/requirements.txt" in output
    assert "line 8" in output
    assert "package7" in output
    assert "package8" in output
    assert "package9" in output
    assert "package10" not in output
    assert "Showing 3 of 36 observations; 33 omitted from this view." in output
    assert "Use --json for the complete retained" in output
    assert "Evidence:" not in output
    assert "python_import" not in output
    assert "dependency_declaration" not in output
    assert len(output) < 5_000


def test_limitations_precede_overview_and_display_omission_stays_distinct(
    checker_inputs,
) -> None:
    report = _check_document(checker_inputs.paths["evidence_heavy_limited_zip"])
    output = _render(report)

    assert output.index("Inspection limitations") < output.index("Overview")
    assert "Maximum nested ZIP depth reached" in output
    assert (
        "At least one nested ZIP was inventoried but not opened beyond depth 3."
        in output
    )
    assert "Showing 3 of 36 observations; 33 omitted from this view." in output
    assert "33 omitted from inspection" not in output
    assert "partial" in output
    assert "Inventory: 1 occurrence" in output
    assert "Rule coverage: 1 skipped input in each of 5 rules" in output
    assert output.count("Nested ZIP depth limit") == 1
    assert "four.zip — four.zip" not in output


def test_zero_observations_are_stated_once_without_a_verdict(checker_inputs) -> None:
    output = _render(_check_document(checker_inputs.paths["minimal_python"]))

    assert output.count("No findings or human-review observations were produced.") == 1
    assert "11 rules total:" in output
    assert "passed" not in output.casefold()
    assert "all clear" not in output.casefold()


def test_observations_remain_visible_beside_parse_failures(tmp_path: Path) -> None:
    source = tmp_path / "parse-failure.py"
    source.write_text(
        "# download https://example.test/data\ndef broken(:\n",
        encoding="utf-8",
    )
    output = _render(_check_document(source))

    assert output.index("Inspection limitations") < output.index("Overview")
    assert "Download comments containing HTTP URLs" in output
    assert "1 human-review observation" in output
    assert "1 human-review topic" in output
    assert "syntax error" in output.casefold()


def test_same_condition_in_different_rules_or_kinds_forms_separate_groups(
    checker_inputs,
) -> None:
    report = copy.deepcopy(_check_document(checker_inputs.paths["evidence_heavy_zip"]))
    archive_result = report["rule_results"][0]
    dependency_result = report["rule_results"][2]
    dependency_result["observations"][0]["condition_code"] = "shared_condition"
    archive_result["observations"].append(
        {
            "observation_id": "observation:36",
            "rule_id": "archive.structure",
            "kind": "finding",
            "condition_code": "shared_condition",
            "member_id": "member:0",
            "source_id": None,
            "line": None,
            "cell": None,
            "snippet": "project/main.py",
            "snippet_truncated": False,
            "evidence_ids": [],
        }
    )

    output = _render(report)

    assert output.count("Shared condition") == 2
    assert "(archive.structure)" in output
    assert "(python.dependencies)" in output
    assert "1 finding" in output
    assert "36 human-review observations" in output


def test_renderer_preserves_literal_markup_and_escapes_terminal_controls(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "report[blue]\u0007\u202e.zip"
    hostile_name = "/[bold]unsafe[/bold]\u0007\u202e.py"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(hostile_name, b"value = 1\n")

    report = _check_document(archive_path)
    output = _render(report)

    assert report["artifact"]["display_name"] == "report[blue]\u0007\u202e.zip"
    assert "report[blue]\\u0007\\u202e.zip" in output
    assert "[bold]unsafe[/bold]\\u0007\\u202e.py" in output
    assert "\u0007" not in output
    assert "\u202e" not in output
    assert "member:" not in output


def test_console_honors_no_color_and_redirected_output(
    monkeypatch,
    checker_inputs,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    class TerminalBuffer(StringIO):
        def isatty(self) -> bool:
            return True

    output = TerminalBuffer()
    console = report_console(file=output)

    render_report(_check_document(checker_inputs.paths["zip64"]), console)

    assert console.no_color is True
    assert console.color_system is None
    assert "\x1b[" not in output.getvalue()

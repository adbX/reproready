"""Terminal contracts for the compact checker report."""

from __future__ import annotations

import copy
import zipfile
from io import BytesIO, StringIO
from pathlib import Path

from rich.console import Console

from reproready.checker import _check_document
from reproready.checker_render import render_report, report_console

REVIEW_TITLES = (
    "Dependencies",
    "Python search paths",
    "Download comments",
    "Files inside archives",
    "CSV files",
    "Notebook setup",
    "Download identifiers",
    "User input",
    "Cloud storage",
)


def _render(
    report: dict[str, object],
    *,
    width: int = 100,
    report_path: Path | None = None,
    show_all: bool = False,
) -> str:
    output = StringIO()
    console = Console(
        file=output,
        color_system=None,
        force_terminal=False,
        no_color=True,
        width=width,
    )
    render_report(
        report,
        console,
        report_path=report_path,
        show_all=show_all,
    )
    return output.getvalue()


def test_evidence_heavy_report_groups_names_and_bounds_compact_detail(
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
    assert output.index("Content analyzed") < output.index("Needs review")
    assert "36 review items in 1 category" in output
    assert "36 occurrences across 36 locations and 36 names" in output
    assert "Declarations without matching imports" in output
    assert "project/requirements.txt / line 8" in output
    assert "package7" in output
    assert "package8" in output
    assert "package9" in output
    assert "package10" not in output
    assert "Showing 3 names and 3 locations from 36 names and 36 locations" in output
    assert "source records" not in output
    assert "python.dependencies" not in output
    assert len(output) < 5_000


def test_limits_are_separate_from_findings_and_keep_nested_location(
    checker_inputs,
) -> None:
    report = _check_document(checker_inputs.paths["evidence_heavy_limited_zip"])
    output = _render(report)

    assert output.index("Content analyzed") < output.index("Limits reached")
    assert output.index("Limits reached") < output.index("Needs review")
    assert output.count("Nested ZIP depth") == 1
    assert "Archive structure, Absolute paths, Dependencies" in output
    assert "one.zip → two.zip → three.zip → four.zip" in output
    assert "resource limit" not in output.casefold()
    assert "36 review items in 1 category" in output


def test_complete_empty_report_has_no_verdict(checker_inputs) -> None:
    output = _render(_check_document(checker_inputs.paths["minimal_python"]))

    assert output.count("No artifact findings reported.") == 1
    assert output.count("No review items reported.") == 1
    assert "passed" not in output.casefold()
    assert "all clear" not in output.casefold()


def test_comment_result_remains_visible_beside_python_parse_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "parse-failure.py"
    source.write_text(
        "# download https://example.test/data\ndef broken(:\n",
        encoding="utf-8",
    )
    output = _render(_check_document(source))

    assert output.index("Checks failed") < output.index("Needs review")
    assert "Python parsing failed" in output
    assert "Download comments" in output
    assert "1 review item in 1 category" in output
    assert "parse-failure.py / line 1" in output
    assert "No review items" not in output


def test_all_nine_review_categories_keep_dependency_conditions(
    checker_inputs,
) -> None:
    report = copy.deepcopy(_check_document(checker_inputs.paths["minimal_python"]))
    observation_number = 0
    for result in report["rule_results"][2:]:
        result["status"] = "complete"
        conditions = (
            ["import_without_exact_declaration", "declaration_without_exact_import"]
            if result["rule_id"] == "python.dependencies"
            else [
                {
                    "python.sys-path-three-dot": "three_dot_path_segment",
                    "python.download-comment-http-url": "download_comment_with_http_url",
                    "python.open-bundled-archive-member": "read_path_only_in_bundled_archive",
                    "python.pandas-csv-inventory-absence": "pandas_csv_not_in_inventory",
                    "python.notebook-pip-install": "notebook_pip_install",
                    "python.gdown-anonymized-value": "anonymized_download_identifier",
                    "python.entry-point-input": "entry_point_stdin",
                    "python.gfile-bucket-authority": "bucket_authority_literal",
                }[result["rule_id"]]
            ]
        )
        for condition in conditions:
            result["observations"].append(
                {
                    "observation_id": f"observation:{observation_number}",
                    "rule_id": result["rule_id"],
                    "kind": "needs_human_review",
                    "condition_code": condition,
                    "member_id": None,
                    "source_id": "source:0",
                    "line": observation_number + 1,
                    "cell": None,
                    "snippet": f"example {observation_number}",
                    "snippet_truncated": False,
                    "evidence_ids": [],
                }
            )
            observation_number += 1

    output = _render(report, width=120)

    assert "10 review items in 9 categories" in output
    for title in REVIEW_TITLES:
        assert title in output
    assert "Imports without matching declarations" in output
    assert "Declarations without matching imports" in output


def test_multiple_imports_on_one_line_have_one_distinct_location(
    tmp_path: Path,
) -> None:
    source = tmp_path / "same-line.py"
    source.write_text("import missing_alpha, missing_beta\n", encoding="utf-8")

    output = _render(_check_document(source))

    assert "2 occurrences across 1 location and 2 names" in output
    assert "same-line.py / line 1" in output


def test_linked_archive_evidence_keeps_both_locations(tmp_path: Path) -> None:
    nested_output = BytesIO()
    with zipfile.ZipFile(nested_output, "w") as nested:
        nested.writestr("data.csv", "a,b\n1,2\n")
    source = tmp_path / "linked.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("main.py", "open('data.csv')\n")
        archive.writestr("bundle.zip", nested_output.getvalue())

    output = _render(_check_document(source))

    assert "Files inside archives" in output
    assert "Related: bundled archive member" in output
    assert "bundle.zip → data.csv" in output


def test_same_condition_in_finding_and_review_stays_in_separate_sections(
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
    assert output.index("Findings") < output.index("Needs review")
    assert "1 artifact finding" in output
    assert "36 review items in 1 category" in output


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


def test_narrow_output_folds_long_identity_and_locations(tmp_path: Path) -> None:
    parts = [f"segment{index:02d}" for index in range(10)]
    source = tmp_path / ("-".join(parts) + ".py")
    source.write_text("open('/tmp/input.csv')\n", encoding="utf-8")

    output = _render(_check_document(source), width=52)

    assert "Artifact" in output
    assert "Name" in output
    assert "line 1" in output
    assert "…" not in output
    for part in parts:
        assert part in output

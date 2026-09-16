"""Subprocess contracts for ``reproready check``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from reproready import cli

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo-artifact"
CHECKER_DEMO = ROOT / "examples" / "checker-demo.py"
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"


def _run_cli(
    *arguments: object, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    command = [
        sys.executable,
        "-m",
        "reproready.cli",
        *(str(item) for item in arguments),
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


def _save_check_report(
    source: Path, destination: Path, *, indent: int | None = None
) -> dict:
    generated = _run_cli("check", source, "--json")
    assert generated.returncode == 0
    document = json.loads(generated.stdout)
    destination.write_text(
        json.dumps(document, indent=indent) + "\n",
        encoding="utf-8",
    )
    return document


@pytest.fixture(scope="module")
def report_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.mark.parametrize(
    "fixture_name",
    ["minimal_python", "valid_notebook", "evidence_heavy_zip", "unsupported_regular"],
)
def test_terminal_check_accepts_one_regular_file(
    checker_inputs, fixture_name: str
) -> None:
    source = checker_inputs.paths[fixture_name]

    result = _run_cli("check", source)

    assert result.returncode == 0
    assert result.stderr == ""
    assert source.name in result.stdout
    assert "Content analyzed" in result.stdout
    assert "\x1b[" not in result.stdout


@pytest.mark.parametrize(
    "fixture_name",
    ["minimal_python", "valid_notebook", "evidence_heavy_zip", "unsupported_regular"],
)
def test_json_check_emits_one_complete_schema_document(
    checker_inputs,
    fixture_name: str,
    report_validator: Draft202012Validator,
) -> None:
    result = _run_cli("check", checker_inputs.paths[fixture_name], "--json")

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.endswith("\n")
    assert "\x1b[" not in result.stdout
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    assert (
        document["artifact"]["display_name"] == checker_inputs.paths[fixture_name].name
    )
    report_validator.validate(document)


def test_json_check_is_deterministic_and_retains_terminal_omissions(
    checker_inputs,
) -> None:
    source = checker_inputs.paths["evidence_heavy_zip"]

    terminal = _run_cli("check", source)
    first = _run_cli("check", source, "--json")
    second = _run_cli("check", source, "--json")
    document = json.loads(first.stdout)
    dependencies = document["rule_results"][2]

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert len(dependencies["observations"]) == 36
    assert len(dependencies["evidence"]) > 43
    assert "package42" in first.stdout
    assert "package42" not in terminal.stdout


@pytest.mark.parametrize(
    ("case", "code", "message"),
    [
        ("missing", "source_not_found", "The input does not exist."),
        ("directory", "not_regular_file", "The checker accepts one regular file."),
        (
            "link",
            "top_level_link",
            "A top-level symbolic link is not a supported checker input.",
        ),
    ],
)
def test_admission_errors_use_exit_two_and_fixed_argparse_style_copy(
    checker_inputs,
    tmp_path: Path,
    case: str,
    code: str,
    message: str,
) -> None:
    if case == "missing":
        source = tmp_path / "private-missing-name.py"
    elif case == "directory":
        source = tmp_path / "private-directory-name"
        source.mkdir()
    else:
        source = checker_inputs.paths["top_level_link"]

    result = _run_cli("check", source)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == f"reproready check: error: {message} ({code})\n"
    assert str(source) not in result.stderr


@pytest.mark.parametrize("extra", ["second.py", "--details", "--validate", "--model"])
def test_check_parser_rejects_extra_paths_and_unapproved_options(
    checker_inputs,
    extra: str,
) -> None:
    result = _run_cli("check", checker_inputs.paths["minimal_python"], extra)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage: reproready" in result.stderr


def test_unexpected_internal_failure_uses_exit_one_without_exception_text(
    checker_inputs,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_source):
        raise RuntimeError("private path /owner/secret")

    monkeypatch.setattr(cli, "check_path", fail)

    exit_code = cli.main(["check", str(checker_inputs.paths["minimal_python"])])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == (
        "reproready check: error: An internal failure prevented a checker report. "
        "(internal_error)\n"
    )
    assert "private" not in captured.err


def test_hostile_artifact_text_is_literal_and_terminal_safe(tmp_path: Path) -> None:
    source = tmp_path / "report[red]\u0007\u202e.py"
    source.write_text("open('/unsafe')\n", encoding="utf-8")

    result = _run_cli("check", source)

    assert result.returncode == 0
    assert "report[red]\\u0007\\u202e.py" in result.stdout
    assert "\u0007" not in result.stdout
    assert "\u202e" not in result.stdout
    assert "\x1b[" not in result.stdout


def test_no_color_keeps_plain_redirected_output(checker_inputs) -> None:
    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"

    result = _run_cli(
        "check",
        checker_inputs.paths["evidence_heavy_zip"],
        env=environment,
    )

    assert result.returncode == 0
    assert "\x1b[" not in result.stdout
    assert "36 review items in 1 category" in result.stdout


def test_saved_view_replays_pretty_v1_report_without_source(tmp_path: Path) -> None:
    source = tmp_path / "temporary-source.py"
    source.write_text("open('/example/data.csv')\n", encoding="utf-8")
    report_path = tmp_path / "report.json"
    _save_check_report(source, report_path, indent=2)
    saved = report_path.read_bytes()
    source.unlink()

    viewed = _run_cli("view", report_path)

    assert viewed.returncode == 0
    assert viewed.stderr == ""
    assert "temporary-source.py" in viewed.stdout
    assert report_path.read_bytes() == saved


def test_saved_view_all_expands_retained_observations(
    checker_inputs, tmp_path: Path
) -> None:
    report_path = tmp_path / "report.json"
    _save_check_report(checker_inputs.paths["evidence_heavy_zip"], report_path)

    compact = _run_cli("view", report_path)
    expanded = _run_cli("view", report_path, "--all")

    assert compact.returncode == expanded.returncode == 0
    assert "package42" not in compact.stdout
    assert report_path.parent.name in compact.stdout
    assert report_path.name in compact.stdout
    assert "…" not in compact.stdout
    assert "package42" in expanded.stdout


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ("{", "invalid_report_json"),
        ("[]", "unsupported_report"),
        ('{"value":NaN}', "invalid_report_json"),
    ],
)
def test_saved_view_rejects_non_report_json(
    tmp_path: Path, payload: str, code: str
) -> None:
    report_path = tmp_path / "private-report-name.json"
    report_path.write_text(payload, encoding="utf-8")

    viewed = _run_cli("view", report_path)

    assert viewed.returncode == 2
    assert viewed.stdout == ""
    assert f"({code})" in viewed.stderr
    assert str(report_path) not in viewed.stderr


def test_saved_view_rejects_links_and_oversized_files(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as output:
        output.truncate(128 * 1024**2 + 1)

    linked = _run_cli("view", link)
    too_large = _run_cli("view", oversized)

    assert linked.returncode == too_large.returncode == 2
    assert linked.stdout == too_large.stdout == ""
    assert "(invalid_report_path)" in linked.stderr
    assert "(report_too_large)" in too_large.stderr


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("schema", "unsupported_schema_version"),
        ("ruleset", "unsupported_ruleset_version"),
        ("rule", "unsupported_rule_id"),
        ("reference", "invalid_report_references"),
    ],
)
def test_saved_view_rejects_incompatible_or_inconsistent_checker_reports(
    tmp_path: Path, change: str, code: str
) -> None:
    report_path = tmp_path / f"{change}.json"
    document = _save_check_report(CHECKER_DEMO, report_path)
    if change == "schema":
        document["schema_version"] = "2"
    elif change == "ruleset":
        document["ruleset_version"] = "future-v1"
    elif change == "rule":
        document["rule_results"][3]["rule_id"] = "python.future-rule"
    else:
        document["rule_results"][1]["observations"][0]["source_id"] = "source:999"
    report_path.write_text(json.dumps(document) + "\n", encoding="utf-8")

    viewed = _run_cli("view", report_path)

    assert viewed.returncode == 2
    assert viewed.stdout == ""
    assert f"({code})" in viewed.stderr


def test_existing_score_command_keeps_single_and_multiple_json_shapes() -> None:
    single = _run_cli("score", DEMO, "--json")
    multiple = _run_cli("score", DEMO, DEMO, "--json")

    assert single.returncode == multiple.returncode == 0
    single_document = json.loads(single.stdout)
    multiple_document = json.loads(multiple.stdout)
    assert single_document["r"] == pytest.approx(0.75**0.25)
    assert single_document["tier"] == "3"
    assert multiple_document == [single_document, single_document]


def test_score_terminal_names_units_stages_and_channels() -> None:
    result = _run_cli("score", DEMO)

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Runnable code units" in result.stdout
    assert "Observable coverage" in result.stdout
    assert "4 of 4 in-scope stages" in result.stdout
    for label in (
        "Environment",
        "Inputs",
        "Execution",
        "Validation",
        "Implementation",
        "Documentation",
    ):
        assert label in result.stdout


def test_score_terminal_uses_readable_narrow_stage_layout() -> None:
    environment = {**os.environ, "COLUMNS": "52", "NO_COLOR": "1"}

    result = _run_cli("score", DEMO, env=environment)

    assert result.returncode == 0
    assert "Stage evidence" in result.stdout
    assert "Environment" in result.stdout
    assert "Implementation" in result.stdout
    assert "Documentation" in result.stdout
    assert "requirements.txt" in result.stdout


def test_score_terminal_distinguishes_na_and_out_of_scope(tmp_path: Path) -> None:
    artifact = tmp_path / "no-code"
    artifact.mkdir()
    (artifact / "README.md").write_text("# Notes\n", encoding="utf-8")

    result = _run_cli("score", artifact)

    assert result.returncode == 0
    assert "Score                N/A" in result.stdout
    assert "Tier                 N/A" in result.stdout
    assert "Out of scope" in result.stdout


def test_score_terminal_escapes_path_markup_and_controls(tmp_path: Path) -> None:
    artifact = tmp_path / "[bold]\x1b[31m"
    artifact.mkdir()
    (artifact / "run.py").write_text("print('ok')\n", encoding="utf-8")
    environment = {**os.environ, "NO_COLOR": "1"}

    result = _run_cli("score", artifact, env=environment)

    assert result.returncode == 0
    assert "[bold]" in result.stdout
    assert "\\u001b" in result.stdout
    assert "\x1b[" not in result.stdout


def test_help_distinguishes_check_view_and_score() -> None:
    root = _run_cli("--help")
    check = _run_cli("check", "--help")
    view = _run_cli("view", "--help")
    score = _run_cli("score", "--help")

    assert (
        root.returncode == check.returncode == view.returncode == score.returncode == 0
    )
    assert "Inspect artifacts without running their code" in root.stdout
    assert "does not calculate the ReproReady score" in check.stdout
    assert "without reopening or inspecting the original artifact" in view.stdout
    assert "Scoring is separate from checker observations" in score.stdout

"""Package-data, version, and worked-example contracts for the checker."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import json
import re
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

import reproready
from reproready import CheckReport, check_path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / "examples" / "checker-demo.py"
EXAMPLE_REPORT_PATH = ROOT / "examples" / "checker-demo-report.json"


def _schema() -> dict[str, object]:
    resource = importlib.resources.files("reproready").joinpath(
        "schemas/check-report-v1.schema.json"
    )
    return json.loads(resource.read_text(encoding="utf-8"))


def _without_runtime(document: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in document.items() if key != "runtime"}


def test_packaged_schema_resource_is_valid_and_stays_version_one() -> None:
    schema = _schema()

    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"] == {"const": "1"}


def test_installed_generated_and_exported_versions_agree() -> None:
    report = check_path(EXAMPLE_PATH)

    assert importlib.metadata.version("reproready") == "0.2.0"
    assert reproready.__version__ == "0.2.0"
    assert report.tool_version == "0.2.0"
    assert report.schema_version == "1"
    assert report.ruleset_version == "python-v1"


def test_worked_example_report_is_exact_and_schema_valid() -> None:
    saved = json.loads(EXAMPLE_REPORT_PATH.read_text(encoding="utf-8"))
    generated = check_path(EXAMPLE_PATH)

    assert isinstance(generated, CheckReport)
    Draft202012Validator(_schema()).validate(saved)
    assert _without_runtime(saved) == _without_runtime(generated.to_dict())

    observations = [
        (observation["kind"], observation["condition_code"])
        for result in generated.rule_results
        for observation in result["observations"]
    ]
    assert observations == [
        ("finding", "posix_absolute_path"),
        ("needs_human_review", "three_dot_path_segment"),
        ("needs_human_review", "download_comment_with_http_url"),
    ]


def test_documented_terminal_example_exposes_groups_counts_and_json_route() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "reproready.cli", "check", str(EXAMPLE_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert re.search(r"Artifact findings\s+1", result.stdout)
    assert re.search(r"Review items\s+2", result.stdout)
    assert re.search(r"Categories\s+2", result.stdout)
    assert "POSIX absolute paths" in result.stdout
    assert "Python search paths" in result.stdout
    assert "Download comments" in result.stdout
    assert "Content analyzed" in result.stdout

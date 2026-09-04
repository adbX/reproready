"""Executable expectations for the public checker report contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"
FIXTURE_DIR = ROOT / "tests/fixtures/checker-report-v1"
RULE_IDS = ["archive.structure", "python.absolute-path", "python.dependencies"]


def _load_json(path: Path) -> dict:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    with path.open(encoding="utf-8") as handle:
        return json.load(handle, parse_constant=reject_constant)


@pytest.fixture(scope="module")
def schema() -> dict:
    return _load_json(SCHEMA_PATH)


@pytest.fixture(scope="module")
def reports() -> list[dict]:
    return [_load_json(path) for path in sorted(FIXTURE_DIR.glob("*.json"))]


def test_schema_is_valid(schema: dict) -> None:
    Draft202012Validator.check_schema(schema)


def test_synthetic_reports_validate(schema: dict, reports: list[dict]) -> None:
    validator = Draft202012Validator(schema)
    for report in reports:
        validator.validate(report)


def test_supported_basename_may_contain_backslash(
    schema: dict, reports: list[dict]
) -> None:
    report = copy.deepcopy(
        next(item for item in reports if item["artifact"]["snapshot_complete"])
    )
    report["artifact"]["display_name"] = "example\\file.py"
    Draft202012Validator(schema).validate(report)


def test_incomplete_snapshot_is_unclassified(reports: list[dict]) -> None:
    report = next(item for item in reports if not item["artifact"]["snapshot_complete"])
    assert report["artifact"]["detected_kind"] == "unclassified"
    assert report["artifact"]["sha256"] is None


def test_snippet_truncation_does_not_reduce_coverage(
    schema: dict, reports: list[dict]
) -> None:
    report = copy.deepcopy(
        next(
            item
            for item in reports
            if item["artifact"]["detected_kind"] == "direct_python"
            and item["artifact"]["snapshot_complete"]
        )
    )
    report["rule_results"][1]["observations"] = [
        {
            "observation_id": "observation:0",
            "rule_id": "python.absolute-path",
            "kind": "finding",
            "condition_code": "posix_absolute_path",
            "member_id": None,
            "source_id": "source:0",
            "line": 1,
            "cell": None,
            "snippet": "x" * 240,
            "snippet_truncated": True,
            "evidence_ids": [],
        }
    ]
    Draft202012Validator(schema).validate(report)


@pytest.mark.parametrize(
    "case",
    [
        "missing_version",
        "unknown_property",
        "invalid_hash",
        "invalid_status",
        "zero_line",
        "zero_cell",
        "oversized_snippet",
        "forbidden_no_finding",
        "absolute_display_name",
        "incomplete_snapshot_with_hash",
        "wrong_rule_order",
        "complete_with_failed_input",
        "partial_without_coverage_gap",
        "inspected_source_with_reason",
        "complete_snapshot_unclassified",
        "error_with_observation",
    ],
)
def test_invalid_reports_are_rejected(
    schema: dict, reports: list[dict], case: str
) -> None:
    report = copy.deepcopy(
        next(
            item
            for item in reports
            if item["artifact"]["detected_kind"] == "zip"
            and item["inventory"]["status"] == "partial"
        )
    )

    if case == "missing_version":
        report.pop("schema_version")
    elif case == "unknown_property":
        report["generated_at"] = "2026-01-01T00:00:00Z"
    elif case == "invalid_hash":
        report["artifact"]["sha256"] = "not-a-digest"
    elif case == "invalid_status":
        report["rule_results"][0]["status"] = "passed"
    elif case == "zero_line":
        report["rule_results"][1]["observations"][0]["line"] = 0
    elif case == "zero_cell":
        report["source_index"][0]["cell"] = 0
    elif case == "oversized_snippet":
        report["rule_results"][0]["observations"][0]["snippet"] = "x" * 241
    elif case == "forbidden_no_finding":
        report["rule_results"][0]["no_finding"] = False
    elif case == "absolute_display_name":
        report["artifact"]["display_name"] = "/private/input.zip"
    elif case == "incomplete_snapshot_with_hash":
        report["artifact"]["snapshot_complete"] = False
    elif case == "wrong_rule_order":
        report["rule_results"][0], report["rule_results"][1] = (
            report["rule_results"][1],
            report["rule_results"][0],
        )
    elif case == "complete_with_failed_input":
        report["rule_results"][1]["status"] = "complete"
    elif case == "partial_without_coverage_gap":
        report["rule_results"][1]["skipped_inputs"] = []
        report["rule_results"][1]["failed_inputs"] = []
    elif case == "inspected_source_with_reason":
        report["source_index"][0]["reason_code"] = "syntax_error"
    elif case == "complete_snapshot_unclassified":
        report["artifact"]["detected_kind"] = "unclassified"
    elif case == "error_with_observation":
        report["rule_results"][0]["status"] = "error"
        report["rule_results"][0]["failed_inputs"] = [
            {
                "member_id": None,
                "source_id": None,
                "reason_code": "worker_error",
                "message": "The worker stopped before producing a usable rule result.",
            }
        ]

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(report)


def test_report_references_and_identifiers_are_consistent(reports: list[dict]) -> None:
    for report in reports:
        assert [result["rule_id"] for result in report["rule_results"]] == RULE_IDS
        assert [parser["name"] for parser in report["runtime"]["parsers"]] == sorted(
            parser["name"] for parser in report["runtime"]["parsers"]
        )

        member_ids = {member["member_id"] for member in report["inventory"]["members"]}
        source_ids = {source["source_id"] for source in report["source_index"]}
        evidence = [
            item for result in report["rule_results"] for item in result["evidence"]
        ]
        observations = [
            item for result in report["rule_results"] for item in result["observations"]
        ]
        evidence_ids = {item["evidence_id"] for item in evidence}

        assert len(member_ids) == len(report["inventory"]["members"])
        assert len(source_ids) == len(report["source_index"])
        assert len(evidence_ids) == len(evidence)
        assert len({item["observation_id"] for item in observations}) == len(
            observations
        )
        assert len(observations) <= report["limits"]["effective"]["max_observations"]
        assert [member["member_id"] for member in report["inventory"]["members"]] == [
            f"member:{index}" for index in range(len(report["inventory"]["members"]))
        ]
        assert [source["source_id"] for source in report["source_index"]] == [
            f"source:{index}" for index in range(len(report["source_index"]))
        ]
        assert [item["evidence_id"] for item in evidence] == [
            f"evidence:{index}" for index in range(len(evidence))
        ]
        assert [item["observation_id"] for item in observations] == [
            f"observation:{index}" for index in range(len(observations))
        ]

        for member in report["inventory"]["members"]:
            assert (
                member["parent_member_id"] is None
                or member["parent_member_id"] in member_ids
            )
        for issue in report["inventory"]["issues"]:
            assert issue["member_id"] is None or issue["member_id"] in member_ids
        for source in report["source_index"]:
            assert source["member_id"] is None or source["member_id"] in member_ids
            assert (source["status"] == "inspected") == (source["reason_code"] is None)
        for item in evidence:
            assert item["member_id"] is None or item["member_id"] in member_ids
            assert item["source_id"] is None or item["source_id"] in source_ids
            assert set(item["related_evidence_ids"]) <= evidence_ids
        for result in report["rule_results"]:
            for observation in result["observations"]:
                assert observation["rule_id"] == result["rule_id"]
                assert (
                    observation["member_id"] is None
                    or observation["member_id"] in member_ids
                )
                assert (
                    observation["source_id"] is None
                    or observation["source_id"] in source_ids
                )
                assert set(observation["evidence_ids"]) <= evidence_ids
            for item in result["skipped_inputs"] + result["failed_inputs"]:
                assert item["member_id"] is None or item["member_id"] in member_ids
                assert item["source_id"] is None or item["source_id"] in source_ids


def test_synthetic_reports_exercise_every_rule_status(reports: list[dict]) -> None:
    statuses = {
        result["status"] for report in reports for result in report["rule_results"]
    }
    assert statuses == {
        "complete",
        "partial",
        "unsupported",
        "error",
        "not_applicable",
    }

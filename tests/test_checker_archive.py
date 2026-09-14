"""Consumer-visible contracts for the archive.structure rule."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from checker_input_fixtures import mutate_source_same_size
from jsonschema import Draft202012Validator

from reproready.checker import _check_document, _encode_check_document
from reproready.checker_archive import archive_structure_result
from reproready.checker_intake import FIXED_LIMITS, SourceSnapshot
from reproready.checker_report import (
    _BoundedRecords,
    encode_report,
    successful_intake_report,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"


@pytest.fixture(scope="module")
def report_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _archive_result(report: dict[str, object]) -> dict[str, object]:
    result = report["rule_results"][0]
    assert result["rule_id"] == "archive.structure"
    return result


def _crash_worker(path: Path, checkpoint) -> bytes:
    path.read_bytes()
    checkpoint("after_source_read")
    raise RuntimeError("synthetic worker failure")


def test_complete_zip64_has_complete_empty_archive_result(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    report = _check_document(checker_inputs.paths["zip64"])
    report_validator.validate(report)

    result = _archive_result(report)
    assert result["status"] == "complete"
    assert result["observations"] == []
    assert result["skipped_inputs"] == []
    assert result["failed_inputs"] == []


def test_hostile_archive_observations_are_member_linked_and_ordered(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    report = _check_document(checker_inputs.paths["hostile_zip"])
    report_validator.validate(report)
    result = _archive_result(report)

    assert [
        (item["member_id"], item["condition_code"], item["snippet"])
        for item in result["observations"]
    ] == [
        ("member:6", "duplicate_member_name", "dup.py"),
        ("member:7", "absolute_member_path", "/absolute.py"),
        ("member:8", "parent_path_segment", "../escape.py"),
        ("member:9", "absolute_member_path", r"C:\root\drive.py"),
        ("member:10", "absolute_member_path", r"\\server\share\unc.py"),
        ("member:11", "absolute_member_path", r"\\?\C:\device.py"),
        ("member:12", "control_character_in_name", "control\nname.py"),
        ("member:14", "symlink_entry", "link.py"),
        ("member:15", "special_entry", "pipe"),
        ("member:21", "resource_limit_reached", "four.zip"),
    ]
    assert [item["observation_id"] for item in result["observations"]] == [
        f"observation:{index}" for index in range(10)
    ]
    assert result["status"] == "partial"
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "nested_zip_depth_limit"
    ]

    directory = next(
        member
        for member in report["inventory"]["members"]
        if member["name"] == "explicit-directory/"
    )
    assert directory["kind"] == "directory"
    assert directory["read_status"] == "not_read"
    assert directory["integrity_status"] == "not_checked"
    assert directory["issues"] == []


def test_only_repeated_duplicate_ordinals_are_findings(checker_inputs) -> None:
    report = _check_document(checker_inputs.paths["hostile_zip"])
    duplicates = [
        member
        for member in report["inventory"]["members"]
        if member["name"] == "dup.py"
    ]
    assert [member["duplicate_ordinal"] for member in duplicates] == [1, 2]
    assert [[issue["code"] for issue in member["issues"]] for member in duplicates] == [
        [],
        ["duplicate_member_name"],
    ]
    assert [
        observation["member_id"]
        for observation in _archive_result(report)["observations"]
        if observation["condition_code"] == "duplicate_member_name"
    ] == ["member:6"]


@pytest.mark.parametrize(
    ("fixture_name", "member_id", "condition", "coverage_kind"),
    [
        ("encrypted_zip", "member:0", "encrypted_entry", "skipped_inputs"),
        (
            "unsupported_compression_zip",
            "member:0",
            "unsupported_compression",
            "skipped_inputs",
        ),
        ("crc_corrupt_zip", "member:0", "integrity_error", "failed_inputs"),
        ("opaque_corrupt_zip", "member:1", "integrity_error", "failed_inputs"),
    ],
)
def test_unreadable_members_are_findings_with_partial_coverage(
    checker_inputs,
    report_validator: Draft202012Validator,
    fixture_name: str,
    member_id: str,
    condition: str,
    coverage_kind: str,
) -> None:
    report = _check_document(checker_inputs.paths[fixture_name])
    report_validator.validate(report)
    result = _archive_result(report)

    assert result["status"] == "partial"
    assert [
        (item["member_id"], item["condition_code"]) for item in result["observations"]
    ] == [(member_id, condition)]
    assert [
        (item["member_id"], item["reason_code"]) for item in result[coverage_kind]
    ] == [(member_id, condition)]
    other = "failed_inputs" if coverage_kind == "skipped_inputs" else "skipped_inputs"
    assert result[other] == []


def test_central_directory_failure_is_a_usable_partial_result(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    report = _check_document(checker_inputs.paths["central_directory_error_zip"])
    report_validator.validate(report)
    result = _archive_result(report)

    assert result["status"] == "partial"
    assert [
        (item["member_id"], item["condition_code"], item["snippet"])
        for item in result["observations"]
    ] == [(None, "central_directory_error", None)]
    assert [
        (item["member_id"], item["reason_code"]) for item in result["failed_inputs"]
    ] == [(None, "central_directory_error")]


def test_archive_applicability_and_parent_failures_remain_distinct(
    checker_inputs, tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    direct = _check_document(checker_inputs.paths["minimal_python"])
    unsupported = _check_document(checker_inputs.paths["unsupported_regular"])
    worker_error = _check_document(
        checker_inputs.paths["minimal_python"], _worker=_crash_worker
    )

    changing = tmp_path / "changing.py"
    changing.write_bytes(checker_inputs.paths["mutable_source"].read_bytes())

    def mutate(checkpoint: str) -> None:
        if checkpoint == checker_inputs.mutation_checkpoint:
            mutate_source_same_size(changing)

    snapshot_error = _check_document(changing, _snapshot_checkpoint=mutate)
    for report in [direct, unsupported, worker_error, snapshot_error]:
        report_validator.validate(report)

    assert _archive_result(direct)["status"] == "not_applicable"
    assert _archive_result(unsupported)["status"] == "unsupported"
    assert _archive_result(worker_error)["status"] == "error"
    assert _archive_result(snapshot_error)["status"] == "error"


def test_observation_cap_keeps_prefix_and_reserved_limit_finding(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    archive_path = tmp_path / "observation-limit.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
        for index in range(FIXED_LIMITS["max_observations"]):
            archive.writestr(f"/unsafe-{index}", b"")

    report = _check_document(archive_path)
    report_validator.validate(report)
    result = _archive_result(report)
    observations = result["observations"]

    assert len(observations) == FIXED_LIMITS["max_observations"]
    assert observations[0]["snippet"] == "/unsafe-0"
    assert observations[-2]["snippet"] == "/unsafe-9998"
    assert observations[-1]["condition_code"] == "resource_limit_reached"
    assert observations[-1]["member_id"] == "member:9999"
    assert observations[-1]["snippet"] == "/unsafe-9999"
    assert [item["observation_id"] for item in observations] == [
        f"observation:{index}" for index in range(len(observations))
    ]
    assert report["limits"]["reached"] == ["max_observations"]
    assert result["status"] == "partial"
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "observation_limit"
    ]
    assert len(_encode_check_document(archive_path)) <= FIXED_LIMITS["max_report_bytes"]


def test_report_cap_keeps_a_schema_valid_prefix_and_reserved_limit_finding(
    report_validator: Draft202012Validator,
) -> None:
    records = _BoundedRecords(
        max_report_bytes=16 * 1024,
        max_observations=FIXED_LIMITS["max_observations"],
        reserve_bytes=8 * 1024,
    )
    members: list[dict[str, object]] = []
    member = {
        "container_id": "container:0",
        "member_id": "member:0",
        "parent_member_id": None,
        "name": "/member.py",
        "duplicate_ordinal": 1,
        "kind": "file",
        "compressed_size": 1,
        "expanded_size": 1,
        "read_status": "complete",
        "integrity_status": "ok",
        "issues": [
            {
                "code": "absolute_member_path",
                "message": "The decoded member name has POSIX absolute-path syntax.",
                "member_id": "member:0",
            }
        ],
    }
    assert records.append(members, member)
    archive_result = archive_structure_result(
        "zip",
        members,
        [],
        [],
        append_record=records.append,
    )

    sources: list[dict[str, object]] = []
    while True:
        source = {
            "source_id": f"source:{len(sources)}",
            "member_id": "member:0",
            "form": "python_file",
            "status": "inspected",
            "cell": None,
            "language": "python",
            "reason_code": None,
        }
        if not records.append(sources, source, member_id="member:0"):
            break

    report = successful_intake_report(
        SourceSnapshot("budget.zip", 1, "a" * 64, Path("unused")),
        detected_kind="zip",
        parsers={"ast", "zipfile"},
        reached_limits=[],
        inventory_status="complete",
        members=members,
        issues=[],
        source_index=sources,
        archive_result=archive_result,
        absolute_path_observations=[],
        absolute_path_limit_candidate=None,
        absolute_path_scan_limited_source={
            "member_id": "member:0",
            "source_id": None,
        },
        records=records,
    )
    encoded = encode_report(report)
    report_validator.validate(report)

    assert len(encoded) <= records.max_report_bytes
    assert [source["source_id"] for source in report["source_index"]] == [
        f"source:{index}" for index in range(len(sources))
    ]
    assert report["limits"]["reached"] == ["max_report_bytes"]
    assert report["inventory"]["status"] == "partial"
    result = _archive_result(report)
    assert [item["condition_code"] for item in result["observations"]] == [
        "absolute_member_path",
        "resource_limit_reached",
    ]
    assert result["observations"][0]["member_id"] == "member:0"
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "report_size_limit"
    ]


def test_repeated_runs_have_stable_report_fields_and_ids(checker_inputs) -> None:
    first = _check_document(checker_inputs.paths["hostile_zip"])
    second = _check_document(checker_inputs.paths["hostile_zip"])

    assert first == second
    observations = [
        item for result in first["rule_results"] for item in result["observations"]
    ]
    assert [item["observation_id"] for item in observations] == [
        f"observation:{index}" for index in range(len(observations))
    ]

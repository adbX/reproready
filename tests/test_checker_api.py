"""Public Python contracts for the static checker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from checker_input_fixtures import mutate_source_same_size
from jsonschema import Draft202012Validator

from reproready import (
    ArtifactReport,
    CheckInputError,
    CheckMember,
    CheckReport,
    check_path,
    checker,
    score_path,
)
from reproready.checker import _check_document

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"
RULE_IDS = (
    "archive.structure",
    "python.absolute-path",
    "python.dependencies",
    "python.sys-path-three-dot",
    "python.download-comment-http-url",
    "python.open-bundled-archive-member",
    "python.pandas-csv-inventory-absence",
    "python.notebook-pip-install",
    "python.gdown-anonymized-value",
    "python.entry-point-input",
    "python.gfile-bucket-authority",
)


def _crash_worker(path: Path, checkpoint) -> bytes:
    path.read_bytes()
    checkpoint("after_source_read")
    raise RuntimeError("synthetic worker failure")


@pytest.fixture(scope="module")
def report_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.mark.parametrize(
    ("fixture_name", "detected_kind"),
    [
        ("minimal_python", "direct_python"),
        ("valid_notebook", "direct_notebook"),
        ("hostile_zip", "zip"),
        ("unsupported_regular", "unsupported_regular"),
    ],
)
def test_check_path_returns_typed_schema_valid_catalogue(
    checker_inputs,
    report_validator: Draft202012Validator,
    fixture_name: str,
    detected_kind: str,
) -> None:
    report = check_path(checker_inputs.paths[fixture_name])
    document = report.to_dict()

    assert isinstance(report, CheckReport)
    assert document["artifact"]["detected_kind"] == detected_kind
    assert [result["rule_id"] for result in report.rule_results] == list(RULE_IDS)
    assert report.schema_version == "1"
    assert report.ruleset_version == "python-v1"
    report_validator.validate(document)


def test_zip_members_are_stable_typed_views_of_the_schema_document(
    checker_inputs,
    report_validator: Draft202012Validator,
) -> None:
    report = check_path(checker_inputs.paths["hostile_zip"])
    document = report.to_dict()
    raw_members = document["inventory"]["members"]

    assert len(report.members) == len(raw_members)
    assert all(isinstance(member, CheckMember) for member in report.members)
    for member, raw in zip(report.members, raw_members, strict=True):
        assert (
            member.container_id,
            member.member_id,
            member.parent_member_id,
            member.name,
            member.duplicate_ordinal,
            member.kind,
            member.compressed_size,
            member.expanded_size,
            member.read_status,
            member.integrity_status,
            member.issues,
        ) == (
            raw["container_id"],
            raw["member_id"],
            raw["parent_member_id"],
            raw["name"],
            raw["duplicate_ordinal"],
            raw["kind"],
            raw["compressed_size"],
            raw["expanded_size"],
            raw["read_status"],
            raw["integrity_status"],
            tuple(raw["issues"]),
        )

    report_validator.validate(document)


def test_fault_documents_remain_typed_valid_reports(
    checker_inputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_validator: Draft202012Validator,
) -> None:
    changing = tmp_path / "changing.py"
    changing.write_bytes(checker_inputs.paths["mutable_source"].read_bytes())
    documents = [
        _check_document(
            changing,
            _snapshot_checkpoint=lambda _checkpoint: mutate_source_same_size(changing),
        ),
        _check_document(
            checker_inputs.paths["minimal_python"],
            _worker=_crash_worker,
        ),
    ]

    for document in documents:
        monkeypatch.setattr(
            "reproready.checker._check_document",
            lambda _source, document=document: document,
        )
        report = check_path(changing)

        assert isinstance(report, CheckReport)
        assert [result["rule_id"] for result in report.rule_results] == list(RULE_IDS)
        assert report.inventory_status == "error"
        report_validator.validate(report.to_dict())


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
def test_rejected_inputs_raise_stable_public_errors(
    checker_inputs,
    tmp_path: Path,
    case: str,
    code: str,
    message: str,
) -> None:
    if case == "missing":
        source = tmp_path / "missing.py"
    elif case == "directory":
        source = tmp_path / "directory"
        source.mkdir()
    else:
        source = checker_inputs.paths["top_level_link"]

    with pytest.raises(CheckInputError) as raised:
        check_path(source)

    assert type(raised.value) is CheckInputError
    assert raised.value.code == code
    assert raised.value.message == message
    assert str(raised.value) == message
    assert str(source) not in str(raised.value)


def test_unsupported_system_is_rejected_before_path_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("reproready.checker_intake.sys.platform", "win32")
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda _path: pytest.fail("unsupported systems must not access the path"),
    )

    with pytest.raises(CheckInputError) as raised:
        check_path("unread.py")

    assert raised.value.code == "unsupported_system"
    assert raised.value.message == "The checker supports only macOS and Linux."


def test_to_dict_reuses_the_completed_document_without_another_inspection(
    checker_inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    produce = checker._check_document

    def counted(source):
        nonlocal calls
        calls += 1
        return produce(source)

    monkeypatch.setattr(checker, "_check_document", counted)
    report = check_path(checker_inputs.paths["minimal_python"])
    first = report.to_dict()
    second = report.to_dict()

    assert calls == 1
    assert first is second
    assert first["artifact"] is report.artifact
    assert first["runtime"] is report.runtime
    assert first["limits"] is report.limits


def test_two_calls_retain_deterministic_fields(checker_inputs) -> None:
    first = check_path(checker_inputs.paths["hostile_zip"]).to_dict()
    second = check_path(checker_inputs.paths["hostile_zip"]).to_dict()

    assert first == second


def test_checker_report_does_not_expose_score_fields(checker_inputs) -> None:
    checker_report = check_path(checker_inputs.paths["minimal_python"])
    score_report = score_path(checker_inputs.paths["hostile_zip"])

    assert isinstance(score_report, ArtifactReport)
    for field_name in ("score", "r", "stage", "tier", "grade", "validation", "model"):
        assert not hasattr(checker_report, field_name)
        assert field_name not in checker_report.to_dict()

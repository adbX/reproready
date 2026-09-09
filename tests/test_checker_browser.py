"""Behavioral tests for bounded checker intake and member browsing."""

from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path

import pytest
from checker_input_fixtures import mutate_source_same_size
from jsonschema import Draft202012Validator

from reproready.checker import intake_report
from reproready.checker_browser import (
    BROWSER_VERSION,
    BrowserProtocolError,
    BrowserSessionClosed,
    open_member_browser,
    parse_request,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"


def _request(session, operation: str, **values):
    return session.request(
        {
            "browser_version": BROWSER_VERSION,
            "artifact_sha256": session.snapshot.sha256,
            "operation": operation,
            **values,
        }
    )


def _hang_worker(path: Path, checkpoint) -> bytes:
    path.read_bytes()
    checkpoint("after_source_read")
    threading.Event().wait()
    raise AssertionError("unreachable")


def _crash_worker(path: Path, checkpoint) -> bytes:
    path.read_bytes()
    checkpoint("after_source_read")
    raise RuntimeError("synthetic worker failure")


@pytest.fixture(scope="module")
def report_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("fixture_name", "detected_kind", "source_form", "source_status"),
    [
        ("minimal_python", "direct_python", "python_file", "inspected"),
        ("syntax_error", "direct_python", "python_file", "error"),
        ("valid_notebook", "direct_notebook", "notebook_document", "inspected"),
        (
            "unsupported_notebook",
            "direct_notebook",
            "notebook_document",
            "unsupported",
        ),
        ("requirements", "direct_requirements", "requirements", "inspected"),
        ("pyproject", "direct_pyproject", "pyproject", "inspected"),
    ],
)
def test_direct_intake_is_classified_parsed_and_schema_valid(
    checker_inputs,
    report_validator: Draft202012Validator,
    fixture_name: str,
    detected_kind: str,
    source_form: str,
    source_status: str,
) -> None:
    with open_member_browser(checker_inputs.paths[fixture_name]) as session:
        report = session.report
        report_validator.validate(report)
        assert report["artifact"]["detected_kind"] == detected_kind
        assert report["inventory"]["status"] == "complete"
        assert report["source_index"][0]["form"] == source_form
        assert report["source_index"][0]["status"] == source_status
        listing = _request(session, "list_members", cursor=None, limit=200)

    assert listing["status"] == "complete"
    assert listing["inventory_complete"] is True
    assert [target["target_id"] for target in listing["targets"]] == ["artifact"]


def test_name_classification_precedes_zip_payload_detection(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    with open_member_browser(checker_inputs.paths["zip_named_python"]) as session:
        report_validator.validate(session.report)
        assert session.report["artifact"]["detected_kind"] == "direct_python"
        assert session.report["source_index"][0]["reason_code"] == "decode_error"

    with open_member_browser(checker_inputs.paths["docx_named_zip"]) as session:
        report_validator.validate(session.report)
        assert session.report["artifact"]["detected_kind"] == "unsupported_docx"
        listing = _request(session, "list_members", cursor=None, limit=200)
        assert listing["targets"][0]["readable"] is False


def test_nested_zip_inventory_preserves_duplicates_and_depth_limit(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    with open_member_browser(checker_inputs.paths["hostile_zip"]) as session:
        report = session.report
        report_validator.validate(report)
        members = report["inventory"]["members"]
        duplicates = [member for member in members if member["name"] == "dup.py"]
        nested = [member for member in members if member["name"].endswith(".zip")]

        assert report["artifact"]["detected_kind"] == "zip"
        assert report["inventory"]["status"] == "partial"
        assert [member["duplicate_ordinal"] for member in duplicates] == [1, 2]
        assert [member["container_id"] for member in nested] == [
            "container:0",
            "container:1",
            "container:2",
            "container:3",
        ]
        assert nested[-1]["read_status"] == "unsupported"
        assert "max_nested_zip_depth" in report["limits"]["reached"]
        assert [member["member_id"] for member in members] == [
            f"member:{index}" for index in range(len(members))
        ]


def test_duplicate_members_are_read_by_identity(checker_inputs) -> None:
    with open_member_browser(checker_inputs.paths["hostile_zip"]) as session:
        listing = _request(session, "list_members", cursor=None, limit=200)
        duplicate_ids = [
            target["target_id"]
            for target in listing["targets"]
            if target["name_display"] == "dup.py"
        ]
        first = _request(
            session,
            "read_member",
            target_id=duplicate_ids[0],
            start_line=1,
            max_lines=20,
        )
        second = _request(
            session,
            "read_member",
            target_id=duplicate_ids[1],
            start_line=1,
            max_lines=20,
        )

    assert first["text"] == "1: first = 1"
    assert second["text"] == "1: second = 2"


def test_literal_search_and_bounded_read_use_stable_targets(checker_inputs) -> None:
    with open_member_browser(checker_inputs.paths["hostile_zip"]) as session:
        search = _request(
            session,
            "search_members",
            target_id="all",
            query="requests",
            cursor=None,
            limit=100,
        )
        match = search["matches"][0]
        read = _request(
            session,
            "read_member",
            target_id=match["target_id"],
            start_line=match["line"],
            max_lines=2,
        )

    assert search["status"] == "partial"
    assert search["search_complete"] is True
    assert match["line"] == 1
    assert "requests" in match["snippet_display"]
    assert read["text"].splitlines()[0].endswith("import requests")
    assert read["returned_bytes"] <= 64 * 1024


def test_request_envelope_rejects_digest_and_unknown_fields(checker_inputs) -> None:
    with open_member_browser(checker_inputs.paths["minimal_python"]) as session:
        request = {
            "browser_version": BROWSER_VERSION,
            "artifact_sha256": "0" * 64,
            "operation": "list_members",
            "cursor": None,
            "limit": 1,
        }
        with pytest.raises(BrowserProtocolError) as raised:
            session.request(request)
        assert raised.value.code == "artifact_digest_mismatch"

        request["artifact_sha256"] = session.snapshot.sha256
        request["extra"] = True
        with pytest.raises(BrowserProtocolError) as raised:
            session.request(request)
        assert raised.value.code == "invalid_request"


def test_operation_limit_returns_terminal_response_and_revokes_tools(
    checker_inputs,
) -> None:
    with open_member_browser(checker_inputs.paths["minimal_python"]) as session:
        for _ in range(127):
            response = _request(session, "list_members", cursor=None, limit=1)
            assert response["status"] == "complete"
        terminal = _request(session, "list_members", cursor=None, limit=1)
        assert terminal["status"] == "partial"
        assert terminal["reached_limits"][-1] == "max_browser_operations"
        with pytest.raises(BrowserSessionClosed) as raised:
            _request(session, "list_members", cursor=None, limit=1)
        assert raised.value.code == "session_closed"


@pytest.mark.parametrize(
    ("worker", "elapsed", "rss", "issue_code", "reached"),
    [
        (_hang_worker, 0.1, None, "worker_timeout", "max_elapsed_seconds"),
        (_hang_worker, 2.0, 1, "worker_memory_limit", "max_worker_rss_bytes"),
        (_crash_worker, None, None, "worker_error", None),
    ],
)
def test_contained_worker_failures_become_schema_valid_reports(
    checker_inputs,
    report_validator: Draft202012Validator,
    worker,
    elapsed,
    rss,
    issue_code: str,
    reached: str | None,
) -> None:
    report = intake_report(
        checker_inputs.paths["minimal_python"],
        _worker=worker,
        _max_elapsed_seconds=elapsed,
        _max_worker_rss_bytes=rss,
    )

    report_validator.validate(report)
    assert report["inventory"]["status"] == "error"
    assert report["inventory"]["issues"][0]["code"] == issue_code
    assert report["limits"]["reached"] == ([] if reached is None else [reached])


def test_source_mutation_becomes_schema_valid_incomplete_snapshot_report(
    checker_inputs,
    tmp_path: Path,
    report_validator: Draft202012Validator,
) -> None:
    source = tmp_path / "changing.py"
    source.write_bytes(checker_inputs.paths["mutable_source"].read_bytes())

    report = intake_report(
        source,
        _snapshot_checkpoint=lambda _checkpoint: mutate_source_same_size(source),
    )

    report_validator.validate(report)
    assert report["artifact"]["snapshot_complete"] is False
    assert report["artifact"]["sha256"] is None
    assert report["inventory"]["issues"][0]["code"] == "source_changed"


@pytest.mark.parametrize(
    ("fixture_name", "inventory_status", "member_status", "issue_code"),
    [
        ("zip64", "complete", "complete", None),
        ("encrypted_zip", "complete", "unsupported", "encrypted_entry"),
        (
            "unsupported_compression_zip",
            "complete",
            "unsupported",
            "unsupported_compression",
        ),
        ("crc_corrupt_zip", "complete", "error", "integrity_error"),
    ],
)
def test_zip_variants_have_explicit_bounded_member_states(
    checker_inputs,
    report_validator: Draft202012Validator,
    fixture_name: str,
    inventory_status: str,
    member_status: str,
    issue_code: str | None,
) -> None:
    with open_member_browser(checker_inputs.paths[fixture_name]) as session:
        report = session.report
        report_validator.validate(report)
        member = report["inventory"]["members"][0]

    assert report["inventory"]["status"] == inventory_status
    assert member["read_status"] == member_status
    assert [issue["code"] for issue in member["issues"]] == (
        [] if issue_code is None else [issue_code]
    )


def test_central_directory_failure_is_archive_level_and_schema_valid(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    with open_member_browser(
        checker_inputs.paths["central_directory_error_zip"]
    ) as session:
        report = session.report

    report_validator.validate(report)
    assert report["inventory"] == {
        "status": "error",
        "members": [],
        "issues": [
            {
                "code": "central_directory_error",
                "message": "The ZIP central directory could not be read completely.",
                "member_id": None,
            }
        ],
    }


def test_search_finds_literal_across_stream_chunk_boundary(tmp_path: Path) -> None:
    source = tmp_path / "boundary.py"
    source.write_text("# " + "x" * 65_530 + "needle\n", encoding="utf-8")

    with open_member_browser(source) as session:
        response = _request(
            session,
            "search_members",
            target_id="artifact",
            query="needle",
            cursor=None,
            limit=1,
        )

    assert response["search_complete"] is False
    assert response["matches"][0]["byte_offset"] == 65_532
    assert response["matches"][0]["line"] == 1


def test_read_member_truncates_overlong_utf8_line_without_splitting_codepoint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "long-line.py"
    source.write_text("# " + "é" * 40_000 + "\nnext = 1\n", encoding="utf-8")

    with open_member_browser(source) as session:
        response = _request(
            session,
            "read_member",
            target_id="artifact",
            start_line=1,
            max_lines=2,
        )

    assert response["status"] == "complete"
    assert response["text_truncated"] is True
    assert response["returned_bytes"] <= 64 * 1024
    assert response["next_line"] == 2
    response["text"].encode("utf-8")


def test_browser_escapes_terminal_controls_and_rich_markup(checker_inputs) -> None:
    with open_member_browser(checker_inputs.paths["terminal_text"]) as session:
        response = _request(
            session,
            "read_member",
            target_id="artifact",
            start_line=1,
            max_lines=2,
        )

    assert "\\[bold]unsafe\\[/bold]" in response["text"]
    assert "\\u0007" in response["text"]
    assert "\\u202e" in response["text"]


def test_search_reports_undecodable_target_without_replacement_text(
    checker_inputs,
) -> None:
    with open_member_browser(checker_inputs.paths["undecodable_python"]) as session:
        response = _request(
            session,
            "search_members",
            target_id="artifact",
            query="value",
            cursor=None,
            limit=10,
        )

    assert response["status"] == "partial"
    assert response["matches"] == []
    assert response["skipped_targets"] == [
        {"target_id": "artifact", "reason_code": "decode_error"}
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"browser_version":"pilot-v1","browser_version":"pilot-v1"}',
        b'{"browser_version":"pilot-v1","artifact_sha256":"x","operation":NaN}',
        b"[]",
        b'{"browser_version":"pilot-v1","artifact_sha256":"'
        + b"a" * 64
        + b'","operation":"unknown"}',
    ],
)
def test_raw_browser_requests_reject_non_strict_or_invalid_json(raw: bytes) -> None:
    with pytest.raises(BrowserProtocolError) as raised:
        parse_request(raw, "a" * 64)
    assert raised.value.message == "The browser request is invalid."


def test_member_name_reports_every_applicable_safety_issue(tmp_path: Path) -> None:
    artifact = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("/../unsafe\u0001.py", "print('unsafe name')\n")
    report = intake_report(artifact)
    assert [issue["code"] for issue in report["inventory"]["members"][0]["issues"]] == [
        "absolute_member_path",
        "parent_path_segment",
        "control_character_in_name",
    ]


def test_invalid_requests_cannot_bypass_session_operation_limit(checker_inputs) -> None:
    with open_member_browser(checker_inputs.paths["minimal_python"]) as session:
        invalid = {
            "browser_version": BROWSER_VERSION,
            "artifact_sha256": session.snapshot.sha256,
            "operation": "unknown",
        }
        for _ in range(128):
            with pytest.raises(BrowserProtocolError):
                session.request(invalid)
        with pytest.raises(BrowserSessionClosed):
            session.request(invalid)

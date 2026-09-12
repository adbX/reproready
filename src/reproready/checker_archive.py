"""Map bounded ZIP intake facts to the archive.structure rule."""

from __future__ import annotations

from collections.abc import Callable

_AppendRecord = Callable[..., bool]

_ARCHIVE_FINDINGS = {
    "absolute_member_path",
    "parent_path_segment",
    "control_character_in_name",
    "duplicate_member_name",
    "symlink_entry",
    "special_entry",
    "encrypted_entry",
    "unsupported_compression",
    "integrity_error",
}
_UNSUPPORTED_KINDS = {
    "unsupported_tar",
    "unsupported_gzip",
    "unsupported_pdf",
    "unsupported_docx",
    "unsupported_regular",
}
_ARCHIVE_LIMITS = (
    "max_member_count",
    "max_nested_zip_depth",
    "max_expanded_bytes_per_member",
    "max_expanded_bytes_total",
    "max_worker_rss_bytes",
    "max_temporary_bytes",
    "max_elapsed_seconds",
    "max_observations",
    "max_report_bytes",
)
_LIMIT_REASONS = {
    "max_member_count": (
        "member_count_limit",
        "The fixed member-count limit prevented complete archive inspection.",
    ),
    "max_nested_zip_depth": (
        "nested_zip_depth_limit",
        "The nested ZIP was not opened beyond depth 3.",
    ),
    "max_expanded_bytes_per_member": (
        "expanded_member_limit",
        "The member exceeded the fixed expanded-byte limit.",
    ),
    "max_expanded_bytes_total": (
        "expanded_total_limit",
        "The fixed artifact-wide expanded-byte limit prevented inspection.",
    ),
    "max_worker_rss_bytes": (
        "worker_memory_limit",
        "The worker reached the fixed resident-memory limit.",
    ),
    "max_temporary_bytes": (
        "temporary_storage_limit",
        "The fixed checker-owned storage limit prevented inspection.",
    ),
    "max_elapsed_seconds": (
        "worker_timeout",
        "The worker reached the fixed elapsed-time limit.",
    ),
    "max_observations": (
        "observation_limit",
        "The fixed observation limit prevented complete reporting.",
    ),
    "max_report_bytes": (
        "report_size_limit",
        "The fixed encoded-report limit prevented complete reporting.",
    ),
}
_COVERAGE_MESSAGES = {
    "encrypted_entry": "The encrypted archive entry was not read.",
    "unsupported_compression": (
        "The archive entry could not be read with the running ZIP reader."
    ),
    "integrity_error": "The archive entry could not be read and verified completely.",
    "central_directory_error": "The ZIP central directory could not be read completely.",
    "member_read_error": "A checker I/O failure prevented complete archive inspection.",
}


def _ordinal(identifier: str | None) -> int:
    return -1 if identifier is None else int(identifier.split(":", 1)[1])


def _coverage_key(item: dict[str, object]) -> tuple[int, str, str]:
    return (
        _ordinal(item["member_id"]),
        str(item["reason_code"]),
        str(item["message"]),
    )


def _coverage_item(
    reason_code: str,
    message: str,
    *,
    member_id: str | None = None,
) -> dict[str, object]:
    return {
        "member_id": member_id,
        "source_id": None,
        "reason_code": reason_code,
        "message": message,
    }


def _observation(
    condition_code: str,
    member: dict[str, object] | None,
) -> dict[str, object]:
    name = None if member is None else str(member["name"])
    snippet = None if name is None else name[:240]
    return {
        "observation_id": "observation:9999",
        "rule_id": "archive.structure",
        "kind": "finding",
        "condition_code": condition_code,
        "member_id": None if member is None else member["member_id"],
        "source_id": None,
        "line": None,
        "cell": None,
        "snippet": snippet,
        "snippet_truncated": name is not None and len(name) > 240,
        "evidence_ids": [],
    }


def archive_limit_records(
    limit: str,
    member: dict[str, object] | None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Return the reserved observation and coverage record for one limit."""

    reason_code, message = _LIMIT_REASONS[limit]
    member_id = None if member is None else str(member["member_id"])
    return (
        _observation("resource_limit_reached", member),
        _coverage_item(reason_code, message, member_id=member_id),
    )


def _append(
    collection: list[dict[str, object]],
    record: dict[str, object],
    append_record: _AppendRecord | None,
    *,
    observation: bool = False,
) -> bool:
    if append_record is None:
        collection.append(record)
        return True
    return append_record(
        collection,
        record,
        member_id=record["member_id"],
        source_id=record.get("source_id"),
        observation=observation,
        rule_id="archive.structure",
    )


def sort_archive_result(result: dict[str, object]) -> None:
    """Restore deterministic order after a reserved terminal record is added."""

    result["observations"].sort(
        key=lambda item: (
            _ordinal(item["member_id"]),
            item["condition_code"],
            "" if item["snippet"] is None else item["snippet"],
        )
    )
    result["skipped_inputs"].sort(key=_coverage_key)
    result["failed_inputs"].sort(key=_coverage_key)


def archive_structure_result(
    detected_kind: str,
    members: list[dict[str, object]],
    inventory_issues: list[dict[str, object]],
    reached_limits: list[str],
    *,
    append_record: _AppendRecord | None = None,
) -> dict[str, object]:
    """Return one archive rule result from completed intake facts only."""

    observations: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    result: dict[str, object] = {
        "rule_id": "archive.structure",
        "status": "complete",
        "evidence": [],
        "observations": observations,
        "skipped_inputs": skipped,
        "failed_inputs": failed,
    }
    if detected_kind in _UNSUPPORTED_KINDS:
        result["status"] = "unsupported"
        _append(
            skipped,
            _coverage_item(
                "unsupported_format",
                "The top-level regular file is not ZIP or ZIP64.",
            ),
            append_record,
        )
        return result
    if detected_kind != "zip":
        result["status"] = "not_applicable"
        return result

    member_by_id = {str(member["member_id"]): member for member in members}
    candidate_observations: list[dict[str, object]] = []
    candidate_skipped: list[dict[str, object]] = []
    candidate_failed: list[dict[str, object]] = []

    for member in members:
        member_id = str(member["member_id"])
        for issue in member["issues"]:
            code = str(issue["code"])
            if code in _ARCHIVE_FINDINGS:
                candidate_observations.append(_observation(code, member))
            if code in {"encrypted_entry", "unsupported_compression"}:
                candidate_skipped.append(
                    _coverage_item(
                        code,
                        _COVERAGE_MESSAGES[code],
                        member_id=member_id,
                    )
                )
            elif code in {"integrity_error", "member_read_error"}:
                candidate_failed.append(
                    _coverage_item(
                        code,
                        _COVERAGE_MESSAGES[code],
                        member_id=member_id,
                    )
                )

    for issue in inventory_issues:
        if issue["code"] != "central_directory_error":
            continue
        member_id = issue["member_id"]
        member = None if member_id is None else member_by_id.get(str(member_id))
        candidate_observations.append(_observation("central_directory_error", member))
        candidate_failed.append(
            _coverage_item(
                "central_directory_error",
                _COVERAGE_MESSAGES["central_directory_error"],
                member_id=None if member_id is None else str(member_id),
            )
        )

    issue_member_by_code = {
        str(issue["code"]): issue["member_id"] for issue in inventory_issues
    }
    for limit in _ARCHIVE_LIMITS:
        if limit not in reached_limits:
            continue
        reason_code, message = _LIMIT_REASONS[limit]
        member_id = issue_member_by_code.get(reason_code)
        member = None if member_id is None else member_by_id.get(str(member_id))
        candidate_observations.append(_observation("resource_limit_reached", member))
        candidate_skipped.append(
            _coverage_item(
                reason_code,
                message,
                member_id=None if member_id is None else str(member_id),
            )
        )

    candidate_observations.sort(
        key=lambda item: (
            _ordinal(item["member_id"]),
            item["condition_code"],
            "" if item["snippet"] is None else item["snippet"],
        )
    )
    candidate_skipped.sort(key=_coverage_key)
    candidate_failed.sort(key=_coverage_key)
    for observation in candidate_observations:
        if not _append(
            observations,
            observation,
            append_record,
            observation=True,
        ):
            break
    for coverage in candidate_skipped:
        if not _append(skipped, coverage, append_record):
            break
    for coverage in candidate_failed:
        if not _append(failed, coverage, append_record):
            break
    if skipped or failed:
        result["status"] = "partial"
    return result

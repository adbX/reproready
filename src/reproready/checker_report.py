"""Frozen report-shape helpers for bounded checker intake."""

from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .checker_intake import FIXED_LIMITS, SourceSnapshot, WorkerOutcome

SCHEMA_VERSION = "1"
RULESET_VERSION = "python-v1"
RULE_IDS = (
    "archive.structure",
    "python.absolute-path",
    "python.dependencies",
)
_REQUIREMENTS_NAME = re.compile(r"requirements.*\.txt", re.IGNORECASE)
_UNSUPPORTED_KINDS = {
    "unsupported_tar",
    "unsupported_gzip",
    "unsupported_pdf",
    "unsupported_docx",
    "unsupported_regular",
}


def tool_version() -> str:
    """Return the installed package version without importing package exports."""

    try:
        return version("reproready")
    except PackageNotFoundError:
        return "0.1.0"


def classify_name(display_name: str) -> str | None:
    """Apply every basename-only classification rule in frozen precedence order."""

    folded = display_name.casefold()
    if folded.endswith(".py"):
        return "direct_python"
    if folded.endswith(".ipynb"):
        return "direct_notebook"
    if _REQUIREMENTS_NAME.fullmatch(display_name):
        return "direct_requirements"
    if folded == "pyproject.toml":
        return "direct_pyproject"
    if folded.endswith((".tar", ".tar.gz", ".tgz")):
        return "unsupported_tar"
    if folded.endswith(".gz"):
        return "unsupported_gzip"
    if folded.endswith(".pdf"):
        return "unsupported_pdf"
    if folded.endswith(".docx"):
        return "unsupported_docx"
    return None


def classify_snapshot(display_name: str, snapshot_path: Path) -> str:
    """Classify one complete snapshot without allowing content to override names."""

    named_kind = classify_name(display_name)
    if named_kind is not None:
        return named_kind
    if zipfile.is_zipfile(snapshot_path):
        return "zip"
    return "unsupported_regular"


def runtime_record(parsers: Iterable[str]) -> dict[str, object]:
    """Build the deterministic runtime record for parsers actually used."""

    python_version = platform.python_version()
    return {
        "system": "macos" if sys.platform == "darwin" else "linux",
        "python": {
            "implementation": platform.python_implementation(),
            "version": python_version,
        },
        "parsers": [
            {"name": parser, "version": python_version}
            for parser in sorted(set(parsers))
        ],
    }


def _coverage_item(
    reason_code: str,
    message: str,
    *,
    member_id: str | None = None,
    source_id: str | None = None,
) -> dict[str, object]:
    return {
        "member_id": member_id,
        "source_id": source_id,
        "reason_code": reason_code,
        "message": message,
    }


def _rule_result(
    rule_id: str,
    status: str,
    *,
    skipped: list[dict[str, object]] | None = None,
    failed: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "status": status,
        "evidence": [],
        "observations": [],
        "skipped_inputs": skipped or [],
        "failed_inputs": failed or [],
    }


def _unsupported_rule_results() -> list[dict[str, object]]:
    messages = (
        "The top-level regular file is not ZIP or ZIP64.",
        "The top-level regular file is not a supported Python input.",
        "The top-level regular file is not a supported dependency or Python input.",
    )
    return [
        _rule_result(
            rule_id,
            "unsupported",
            skipped=[_coverage_item("unsupported_format", message)],
        )
        for rule_id, message in zip(RULE_IDS, messages, strict=True)
    ]


def pending_rule_results(
    detected_kind: str,
    inventory_status: str,
    source_index: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Represent rules deliberately deferred until after rule discovery."""

    if detected_kind in _UNSUPPORTED_KINDS:
        return _unsupported_rule_results()

    pending = _coverage_item(
        "rule_not_run",
        "This rule is deferred while bounded intake and member browsing are implemented.",
    )
    archive = (
        _rule_result("archive.structure", "partial", skipped=[pending])
        if detected_kind == "zip"
        else _rule_result("archive.structure", "not_applicable")
    )

    python_sources = [
        source
        for source in source_index
        if source["form"] in {"python_file", "notebook_code_cell"}
    ]
    dependency_sources = [
        source
        for source in source_index
        if source["form"]
        in {"python_file", "notebook_code_cell", "requirements", "pyproject"}
    ]

    def deferred_result(
        rule_id: str, sources: list[dict[str, object]], may_be_hidden: bool
    ) -> dict[str, object]:
        if not sources and not may_be_hidden:
            return _rule_result(rule_id, "not_applicable")
        skipped = [
            _coverage_item(
                "rule_not_run",
                "This supported input has not received the deferred rule.",
                member_id=source["member_id"],
                source_id=source["source_id"],
            )
            for source in sources
            if source["status"] == "inspected"
        ]
        failed = [
            _coverage_item(
                str(source["reason_code"]),
                "This supported input could not be inspected completely.",
                member_id=source["member_id"],
                source_id=source["source_id"],
            )
            for source in sources
            if source["status"] != "inspected"
        ]
        if not skipped and not failed:
            skipped = [pending]
        return _rule_result(rule_id, "partial", skipped=skipped, failed=failed)

    hidden = detected_kind == "zip" and inventory_status != "complete"
    absolute_path = deferred_result("python.absolute-path", python_sources, hidden)
    dependencies = deferred_result(
        "python.dependencies", dependency_sources, hidden or detected_kind == "zip"
    )
    return [archive, absolute_path, dependencies]


def successful_intake_report(
    snapshot: SourceSnapshot,
    *,
    detected_kind: str,
    parsers: Iterable[str],
    reached_limits: list[str],
    inventory_status: str,
    members: list[dict[str, object]],
    issues: list[dict[str, object]],
    source_index: list[dict[str, object]],
) -> dict[str, object]:
    """Build one schema-shaped report for completed snapshot intake."""

    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": tool_version(),
        "ruleset_version": RULESET_VERSION,
        "artifact": {
            "display_name": snapshot.display_name,
            "detected_kind": detected_kind,
            "size_bytes": snapshot.size_bytes,
            "sha256": snapshot.sha256,
            "snapshot_complete": True,
        },
        "runtime": runtime_record(parsers),
        "limits": {"effective": dict(FIXED_LIMITS), "reached": reached_limits},
        "inventory": {
            "status": inventory_status,
            "members": members,
            "issues": issues,
        },
        "source_index": source_index,
        "rule_results": pending_rule_results(
            detected_kind, inventory_status, source_index
        ),
    }


def incomplete_snapshot_report(
    *,
    display_name: str,
    size_bytes: int,
    issue_code: str,
    message: str,
    reached_limit: str | None = None,
) -> dict[str, object]:
    """Build a bounded report when no completed source snapshot exists."""

    reached = [reached_limit] if reached_limit is not None else []
    failed_messages = (
        "No archive inspection ran because source snapshotting failed.",
        "No Python inspection ran because source snapshotting failed.",
        "No dependency inspection ran because source snapshotting failed.",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": tool_version(),
        "ruleset_version": RULESET_VERSION,
        "artifact": {
            "display_name": display_name,
            "detected_kind": "unclassified",
            "size_bytes": size_bytes,
            "sha256": None,
            "snapshot_complete": False,
        },
        "runtime": runtime_record([]),
        "limits": {"effective": dict(FIXED_LIMITS), "reached": reached},
        "inventory": {
            "status": "error",
            "members": [],
            "issues": [{"code": issue_code, "message": message, "member_id": None}],
        },
        "source_index": [],
        "rule_results": [
            _rule_result(
                rule_id,
                "error",
                failed=[_coverage_item(issue_code, failure_message)],
            )
            for rule_id, failure_message in zip(RULE_IDS, failed_messages, strict=True)
        ],
    }


def worker_failure_report(
    snapshot: SourceSnapshot,
    outcome: WorkerOutcome,
    *,
    detected_kind: str,
) -> dict[str, object]:
    """Build a small valid report for a contained worker failure."""

    issue_code = outcome.issue_code or "worker_error"
    messages = {
        "worker_start_error": "The inspection worker could not start.",
        "worker_memory_monitor_error": "The inspection worker memory monitor failed.",
        "worker_memory_limit": "The inspection worker reached its resident-memory limit.",
        "worker_timeout": "The inspection worker reached its elapsed-time limit.",
        "worker_report_limit": "The inspection worker reached its report-byte limit.",
        "worker_result_error": "The inspection worker did not produce a usable report.",
        "worker_error": "The inspection worker stopped before producing a usable report.",
    }
    message = messages.get(issue_code, messages["worker_error"])
    reached = [outcome.reached_limit] if outcome.reached_limit is not None else []
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": tool_version(),
        "ruleset_version": RULESET_VERSION,
        "artifact": {
            "display_name": snapshot.display_name,
            "detected_kind": detected_kind,
            "size_bytes": snapshot.size_bytes,
            "sha256": snapshot.sha256,
            "snapshot_complete": True,
        },
        "runtime": runtime_record([]),
        "limits": {"effective": dict(FIXED_LIMITS), "reached": reached},
        "inventory": {
            "status": "error",
            "members": [],
            "issues": [{"code": issue_code, "message": message, "member_id": None}],
        },
        "source_index": [],
        "rule_results": [
            _rule_result(
                rule_id,
                "error",
                failed=[
                    _coverage_item(
                        issue_code,
                        "The worker stopped before producing a usable rule result.",
                    )
                ],
            )
            for rule_id in RULE_IDS
        ],
    }


def encode_report(report: dict[str, object]) -> bytes:
    """Serialize a report deterministically for resource accounting and IPC."""

    return json.dumps(
        report,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")

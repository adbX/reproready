"""Frozen report-shape helpers for bounded checker intake."""

from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .checker_archive import (
    archive_limit_records,
    sort_archive_result,
)
from .checker_intake import FIXED_LIMITS, SourceSnapshot, WorkerOutcome
from .checker_python_dependencies import (
    DeclarationCandidate,
    EvidenceCandidate,
    ImportCandidate,
    ReviewCandidate,
    UnsupportedDependencyContent,
    VirtualFile,
    VirtualLocation,
    analyze_dependencies,
)

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
_REPORT_RESERVE_BYTES = 64 * 1024
_TERMINAL_LIMIT_ISSUES = {
    "max_observations": (
        "observation_limit",
        "The fixed observation limit prevented complete reporting.",
    ),
    "max_report_bytes": (
        "report_size_limit",
        "The fixed encoded-report limit prevented complete reporting.",
    ),
}


class _BoundedRecords:
    """Accept report-array records while reserving one terminal limit result."""

    def __init__(
        self,
        *,
        max_report_bytes: int = FIXED_LIMITS["max_report_bytes"],
        max_observations: int = FIXED_LIMITS["max_observations"],
        reserve_bytes: int = _REPORT_RESERVE_BYTES,
    ) -> None:
        if max_observations < 1:
            raise ValueError("the observation capacity must include a terminal slot")
        if reserve_bytes < 0 or max_report_bytes <= reserve_bytes:
            raise ValueError("the report capacity must exceed its terminal reserve")
        self.max_report_bytes = max_report_bytes
        self.max_observations = max_observations
        self.reserve_bytes = reserve_bytes
        self.record_bytes = 0
        self.observation_count = 0
        self.limit_name: str | None = None
        self.blocked_member_id: str | None = None
        self.blocked_source_id: str | None = None
        self.blocked_rule_id: str | None = None
        self.ordinary_closed = False

    def append(
        self,
        collection: list[dict[str, object]],
        record: dict[str, object],
        *,
        member_id: str | None = None,
        source_id: str | None = None,
        observation: bool = False,
        rule_id: str | None = None,
    ) -> bool:
        """Append one ordinary record if both fixed capacities admit it."""

        if self.ordinary_closed or self.limit_name == "max_report_bytes":
            return False
        if observation and self.limit_name == "max_observations":
            return False
        if observation and self.observation_count >= self.max_observations - 1:
            self._reach(
                "max_observations",
                member_id,
                source_id,
                rule_id or str(record.get("rule_id") or "") or None,
            )
            return False
        encoded_size = len(
            json.dumps(
                record,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        separator_size = 1 if collection else 0
        if (
            self.record_bytes + encoded_size + separator_size + self.reserve_bytes
            > self.max_report_bytes
        ):
            if self.limit_name is None:
                self._reach(
                    "max_report_bytes",
                    member_id,
                    source_id,
                    rule_id or str(record.get("rule_id") or "") or None,
                )
            else:
                self.ordinary_closed = True
            return False
        collection.append(record)
        self.record_bytes += encoded_size + separator_size
        if observation:
            self.observation_count += 1
        return True

    def append_reserved(
        self,
        collection: list[dict[str, object]],
        record: dict[str, object],
    ) -> None:
        """Append terminal state covered by the reserved report capacity."""

        collection.append(record)

    def _reach(
        self,
        limit_name: str,
        member_id: str | None,
        source_id: str | None,
        rule_id: str | None,
    ) -> None:
        self.limit_name = limit_name
        self.blocked_member_id = member_id
        self.blocked_source_id = source_id
        self.blocked_rule_id = rule_id


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


def runtime_record(
    parsers: Iterable[str],
    parser_versions: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build the deterministic runtime record for parsers actually used."""

    python_version = platform.python_version()
    explicit_versions = parser_versions or {}
    return {
        "system": "macos" if sys.platform == "darwin" else "linux",
        "python": {
            "implementation": platform.python_implementation(),
            "version": python_version,
        },
        "parsers": [
            {
                "name": parser,
                "version": explicit_versions.get(parser, python_version),
            }
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


_DISCOVERY_ISSUES = {
    "central_directory_error",
    "member_count_limit",
    "nested_zip_depth_limit",
}
_NESTED_DISCOVERY_ISSUES = {
    "encrypted_entry",
    "unsupported_compression",
    "expanded_member_limit",
    "expanded_total_limit",
    "temporary_storage_limit",
    "integrity_error",
    "checker_cache_error",
    "member_read_error",
}
_FAILED_DISCOVERY_ISSUES = {
    "central_directory_error",
    "integrity_error",
    "checker_cache_error",
    "member_read_error",
}


def _discovery_coverage(
    members: list[dict[str, object]],
    issues: list[dict[str, object]],
) -> list[tuple[str, dict[str, object]]]:
    candidates: list[tuple[str, dict[str, object]]] = []
    seen: set[tuple[str | None, str]] = set()

    def add(code: str, message: str, member_id: str | None) -> None:
        key = member_id, code
        if key in seen:
            return
        seen.add(key)
        field = (
            "failed_inputs" if code in _FAILED_DISCOVERY_ISSUES else "skipped_inputs"
        )
        candidates.append(
            (
                field,
                _coverage_item(code, message, member_id=member_id),
            )
        )

    for member in members:
        name = str(member["name"]).replace("\\", "/").rsplit("/", 1)[-1]
        if not name.casefold().endswith(".zip"):
            continue
        member_id = str(member["member_id"])
        for issue in member["issues"]:
            code = str(issue["code"])
            if code in _NESTED_DISCOVERY_ISSUES:
                add(code, str(issue["message"]), member_id)
    for issue in issues:
        code = str(issue["code"])
        if code in _DISCOVERY_ISSUES:
            member_id = issue["member_id"]
            add(
                code,
                str(issue["message"]),
                None if member_id is None else str(member_id),
            )
    candidates.sort(
        key=lambda item: (
            -1
            if item[1]["member_id"] is None
            else int(str(item[1]["member_id"]).split(":", 1)[1]),
            str(item[1]["reason_code"]),
            str(item[1]["message"]),
        )
    )
    return candidates


def absolute_path_result(
    detected_kind: str,
    members: list[dict[str, object]],
    issues: list[dict[str, object]],
    source_index: list[dict[str, object]],
    observations: list[dict[str, object]],
    records: _BoundedRecords,
    scan_limited_source: dict[str, object] | None,
) -> dict[str, object]:
    """Finalize implemented path findings and coverage independently."""

    if detected_kind in _UNSUPPORTED_KINDS:
        result = _rule_result("python.absolute-path", "unsupported")
        coverage = _coverage_item(
            "unsupported_format",
            "The top-level regular file is not a supported Python input.",
        )
        if not records.append(
            result["skipped_inputs"],
            coverage,
            rule_id="python.absolute-path",
        ):
            records.append_reserved(result["skipped_inputs"], coverage)
        return result

    python_sources = [
        source
        for source in source_index
        if source["form"] in {"python_file", "notebook_code_cell"}
        or (
            source["form"] == "notebook_document"
            and source["status"] in {"skipped", "error"}
        )
    ]
    coverage_candidates: list[tuple[str, dict[str, object]]] = []
    for source in python_sources:
        if source["status"] == "inspected":
            continue
        if source["status"] in {"skipped", "unsupported"}:
            field = "skipped_inputs"
            message = (
                "This applicable Python input was skipped during source inspection."
            )
        else:
            field = "failed_inputs"
            message = "This applicable Python input failed during source inspection."
        coverage_candidates.append(
            (
                field,
                _coverage_item(
                    str(source["reason_code"]),
                    message,
                    member_id=source["member_id"],
                    source_id=source["source_id"],
                ),
            )
        )
    coverage_candidates.extend(_discovery_coverage(members, issues))

    if not python_sources and not coverage_candidates and scan_limited_source is None:
        return _rule_result("python.absolute-path", "not_applicable")

    result = _rule_result(
        "python.absolute-path",
        "partial"
        if coverage_candidates or scan_limited_source is not None
        else "complete",
    )
    result["observations"] = observations
    for field, coverage in coverage_candidates:
        if not records.append(
            result[field],
            coverage,
            member_id=coverage["member_id"],
            source_id=coverage["source_id"],
            rule_id="python.absolute-path",
        ):
            break

    if (
        scan_limited_source is not None
        and records.limit_name is not None
        and records.blocked_rule_id != "python.absolute-path"
    ):
        issue_code, issue_message = _TERMINAL_LIMIT_ISSUES[records.limit_name]
        source_ids = {str(source["source_id"]) for source in source_index}
        source_id = scan_limited_source.get("source_id")
        if source_id not in source_ids:
            source_id = None
        coverage = _coverage_item(
            issue_code,
            issue_message,
            member_id=scan_limited_source.get("member_id"),
            source_id=source_id,
        )
        if not records.append(
            result["skipped_inputs"],
            coverage,
            member_id=coverage["member_id"],
            source_id=coverage["source_id"],
            rule_id="python.absolute-path",
        ):
            records.append_reserved(result["skipped_inputs"], coverage)
    return result


def _dependency_sources(
    source_index: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return [
        source
        for source in source_index
        if source["form"]
        in {
            "python_file",
            "notebook_code_cell",
            "requirements",
            "pyproject",
            "unsupported_source",
        }
        or (
            source["form"] == "notebook_document"
            and source["status"] in {"skipped", "error"}
        )
    ]


def _dependency_source_coverage(
    source: Mapping[str, object],
) -> tuple[str, dict[str, object]] | None:
    if source["status"] == "inspected":
        return None
    form = str(source["form"])
    status = str(source["status"])
    if status in {"skipped", "unsupported"}:
        field = "skipped_inputs"
        if form == "unsupported_source":
            message = "This dependency-file form is indexed but unsupported."
        else:
            message = "This applicable dependency input was skipped during inspection."
    else:
        field = "failed_inputs"
        if form in {"python_file", "notebook_code_cell", "notebook_document"}:
            message = "Imports could not be read from this source."
        else:
            message = "Declarations could not be read from this source."
    return (
        field,
        _coverage_item(
            str(source["reason_code"]),
            message,
            member_id=source["member_id"],
            source_id=source["source_id"],
        ),
    )


def _dependency_observation(
    candidate: ReviewCandidate,
    evidence_id: str,
) -> dict[str, object]:
    return {
        "observation_id": "observation:9999",
        "rule_id": "python.dependencies",
        "kind": "needs_human_review",
        "condition_code": candidate.condition_code,
        "member_id": candidate.member_id,
        "source_id": candidate.source_id,
        "line": candidate.line,
        "cell": candidate.cell,
        "snippet": candidate.snippet,
        "snippet_truncated": candidate.snippet_truncated,
        "evidence_ids": [evidence_id],
    }


def _dependency_limit_source(
    candidate: EvidenceCandidate | ReviewCandidate,
) -> dict[str, object]:
    return {
        "member_id": candidate.member_id,
        "source_id": candidate.source_id,
        "line": candidate.line,
        "cell": candidate.cell,
    }


def dependency_result(
    detected_kind: str,
    members: list[dict[str, object]],
    issues: list[dict[str, object]],
    source_index: list[dict[str, object]],
    imports: Sequence[ImportCandidate],
    declarations: Sequence[DeclarationCandidate],
    unsupported: Sequence[UnsupportedDependencyContent],
    files: Sequence[VirtualFile],
    source_locations: Mapping[str, VirtualLocation],
    records: _BoundedRecords,
    scan_limited_source: dict[str, object] | None,
    *,
    evidence_start: int,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Finalize dependency evidence, review observations, and coverage."""

    if detected_kind in _UNSUPPORTED_KINDS:
        result = _rule_result("python.dependencies", "unsupported")
        coverage = _coverage_item(
            "unsupported_format",
            "The top-level regular file is not a supported dependency or Python input.",
        )
        if not records.append(
            result["skipped_inputs"],
            coverage,
            rule_id="python.dependencies",
        ):
            records.append_reserved(result["skipped_inputs"], coverage)
        return result, None

    sources = _dependency_sources(source_index)
    discovery = _discovery_coverage(members, issues)
    analysis = analyze_dependencies(
        imports,
        declarations,
        unsupported,
        files,
        source_index,
        source_locations,
    )
    if not sources and not discovery and scan_limited_source is None:
        return _rule_result("python.dependencies", "not_applicable"), None

    result = _rule_result("python.dependencies", "complete")
    admitted_by_key: dict[tuple[object, ...], str] = {}
    earliest_match: dict[tuple[tuple[str, tuple[str, ...], str], str], str] = {}
    next_evidence = evidence_start
    output_limited_source: dict[str, object] | None = None
    for candidate in analysis.evidence:
        related = [
            admitted_by_key[key]
            for key in candidate.related_keys
            if key in admitted_by_key
        ]
        if candidate.match_group is not None and candidate.match_role is not None:
            opposite = "declaration" if candidate.match_role == "import" else "import"
            matched_id = earliest_match.get((candidate.match_group, opposite))
            if matched_id is not None:
                related.append(matched_id)
        related = list(dict.fromkeys(related))
        evidence_id = f"evidence:{next_evidence}"
        record = {
            "evidence_id": evidence_id,
            "kind": candidate.kind,
            "member_id": candidate.member_id,
            "source_id": candidate.source_id,
            "line": candidate.line,
            "cell": candidate.cell,
            "value": candidate.value,
            "related_evidence_ids": related,
        }
        if not records.append(
            result["evidence"],
            record,
            member_id=candidate.member_id,
            source_id=candidate.source_id,
            rule_id="python.dependencies",
        ):
            output_limited_source = _dependency_limit_source(candidate)
            break
        admitted_by_key[candidate.key] = evidence_id
        if candidate.match_group is not None and candidate.match_role is not None:
            earliest_match.setdefault(
                (candidate.match_group, candidate.match_role),
                evidence_id,
            )
        next_evidence += 1

    if output_limited_source is None:
        for candidate in analysis.reviews:
            evidence_id = admitted_by_key.get(candidate.evidence_key)
            if evidence_id is None:
                continue
            observation = _dependency_observation(candidate, evidence_id)
            if not records.append(
                result["observations"],
                observation,
                member_id=candidate.member_id,
                source_id=candidate.source_id,
                observation=True,
                rule_id="python.dependencies",
            ):
                output_limited_source = _dependency_limit_source(candidate)
                break

    coverage_candidates = [
        item
        for source in sources
        if (item := _dependency_source_coverage(source)) is not None
    ]
    coverage_candidates.extend(
        (
            "skipped_inputs",
            _coverage_item(
                item.reason_code,
                item.message,
                member_id=item.member_id,
                source_id=item.source_id,
            ),
        )
        for item in analysis.unsupported
    )
    coverage_candidates.extend(discovery)
    if scan_limited_source is not None:
        coverage_candidates.append(
            (
                "skipped_inputs",
                _coverage_item(
                    "report_size_limit",
                    _TERMINAL_LIMIT_ISSUES["max_report_bytes"][1],
                    member_id=scan_limited_source.get("member_id"),
                    source_id=scan_limited_source.get("source_id"),
                ),
            )
        )
    if (
        records.limit_name == "max_report_bytes"
        and records.blocked_rule_id != "python.dependencies"
        and (coverage_candidates or output_limited_source is not None)
    ):
        source = output_limited_source or scan_limited_source
        if source is None:
            source = coverage_candidates[0][1]
        records.append_reserved(
            result["skipped_inputs"],
            _coverage_item(
                "report_size_limit",
                _TERMINAL_LIMIT_ISSUES["max_report_bytes"][1],
                member_id=source.get("member_id"),
                source_id=source.get("source_id"),
            ),
        )
        result["status"] = "partial"
        return result, source
    if (
        output_limited_source is not None
        and records.limit_name is not None
        and records.blocked_rule_id != "python.dependencies"
    ):
        issue_code, issue_message = _TERMINAL_LIMIT_ISSUES[records.limit_name]
        coverage_candidates.append(
            (
                "skipped_inputs",
                _coverage_item(
                    issue_code,
                    issue_message,
                    member_id=output_limited_source.get("member_id"),
                    source_id=output_limited_source.get("source_id"),
                ),
            )
        )
    coverage_candidates.sort(
        key=lambda item: (
            -1
            if item[1]["source_id"] is None
            else int(str(item[1]["source_id"]).split(":", 1)[1]),
            -1
            if item[1]["member_id"] is None
            else int(str(item[1]["member_id"]).split(":", 1)[1]),
            str(item[1]["reason_code"]),
        )
    )
    for field, coverage in coverage_candidates:
        if not records.append(
            result[field],
            coverage,
            member_id=coverage["member_id"],
            source_id=coverage["source_id"],
            rule_id="python.dependencies",
        ):
            if records.blocked_rule_id != "python.dependencies":
                records.append_reserved(result[field], coverage)
            if output_limited_source is None:
                output_limited_source = {
                    "member_id": coverage["member_id"],
                    "source_id": coverage["source_id"],
                }
            break

    if coverage_candidates or output_limited_source is not None:
        result["status"] = "partial"
    return result, output_limited_source


def _issue_key(issue: dict[str, object]) -> tuple[int, str, str]:
    member_id = issue["member_id"]
    member_ordinal = -1 if member_id is None else int(str(member_id).split(":", 1)[1])
    return member_ordinal, str(issue["code"]), str(issue["message"])


def _assign_observation_ids(rule_results: list[dict[str, object]]) -> None:
    next_id = 0
    for result in rule_results:
        for observation in result["observations"]:
            observation["observation_id"] = f"observation:{next_id}"
            next_id += 1


def _path_limit_records(
    limit: str,
    candidate: dict[str, object] | None,
    scan_limited_source: dict[str, object] | None,
    source_ids: set[str],
    member_ids: set[str],
) -> tuple[dict[str, object], dict[str, object]]:
    issue_code, issue_message = _TERMINAL_LIMIT_ISSUES[limit]
    source: dict[str, object] = candidate or scan_limited_source or {}
    source_id = source.get("source_id")
    if source_id not in source_ids:
        source_id = None
    member_id = source.get("member_id")
    if member_id not in member_ids:
        member_id = None
    observation = {
        "observation_id": "observation:9999",
        "rule_id": "python.absolute-path",
        "kind": "finding",
        "condition_code": "resource_limit_reached",
        "member_id": member_id,
        "source_id": source_id,
        "line": source.get("line") if source_id is not None else None,
        "cell": source.get("cell") if source_id is not None else None,
        "snippet": source.get("snippet") if source_id is not None else None,
        "snippet_truncated": (
            bool(source.get("snippet_truncated")) if source_id is not None else False
        ),
        "evidence_ids": [],
    }
    coverage = _coverage_item(
        issue_code,
        issue_message,
        member_id=member_id,
        source_id=source_id,
    )
    return observation, coverage


def _dependency_limit_records(
    limit: str,
    candidate: dict[str, object] | None,
    source_ids: set[str],
    member_ids: set[str],
) -> tuple[dict[str, object], dict[str, object]]:
    issue_code, issue_message = _TERMINAL_LIMIT_ISSUES[limit]
    source = candidate or {}
    source_id = source.get("source_id")
    if source_id not in source_ids:
        source_id = None
    member_id = source.get("member_id")
    if member_id not in member_ids:
        member_id = None
    observation = {
        "observation_id": "observation:9999",
        "rule_id": "python.dependencies",
        "kind": "finding",
        "condition_code": "resource_limit_reached",
        "member_id": member_id,
        "source_id": source_id,
        "line": source.get("line") if source_id is not None else None,
        "cell": source.get("cell") if source_id is not None else None,
        "snippet": None,
        "snippet_truncated": False,
        "evidence_ids": [],
    }
    coverage = _coverage_item(
        issue_code,
        issue_message,
        member_id=member_id,
        source_id=source_id,
    )
    return observation, coverage


def _assert_dependency_references(
    rule_results: Sequence[Mapping[str, object]],
    source_index: Sequence[Mapping[str, object]],
    members: Sequence[Mapping[str, object]],
) -> None:
    evidence = [item for result in rule_results for item in result["evidence"]]
    observations = [item for result in rule_results for item in result["observations"]]
    evidence_ids = [str(item["evidence_id"]) for item in evidence]
    observation_ids = [str(item["observation_id"]) for item in observations]
    if evidence_ids != [f"evidence:{index}" for index in range(len(evidence_ids))]:
        raise RuntimeError("checker evidence IDs must be dense and ordered")
    if observation_ids != [
        f"observation:{index}" for index in range(len(observation_ids))
    ]:
        raise RuntimeError("checker observation IDs must be dense and ordered")
    admitted_evidence = set(evidence_ids)
    admitted_sources = {str(source["source_id"]) for source in source_index}
    admitted_members = {str(member["member_id"]) for member in members}
    for item in evidence:
        current = int(str(item["evidence_id"]).split(":", 1)[1])
        for related in item["related_evidence_ids"]:
            if (
                related not in admitted_evidence
                or int(str(related).split(":", 1)[1]) >= current
            ):
                raise RuntimeError(
                    "checker evidence relationships must be backward-only"
                )
        if item["source_id"] is not None and item["source_id"] not in admitted_sources:
            raise RuntimeError("checker evidence references an omitted source")
        if item["member_id"] is not None and item["member_id"] not in admitted_members:
            raise RuntimeError("checker evidence references an omitted member")
    for item in observations:
        if not set(item["evidence_ids"]) <= admitted_evidence:
            raise RuntimeError("checker observation references omitted evidence")
        if item["source_id"] is not None and item["source_id"] not in admitted_sources:
            raise RuntimeError("checker observation references an omitted source")
        if item["member_id"] is not None and item["member_id"] not in admitted_members:
            raise RuntimeError("checker observation references an omitted member")
    dependency = next(
        result for result in rule_results if result["rule_id"] == "python.dependencies"
    )
    for field in ("skipped_inputs", "failed_inputs"):
        for item in dependency[field]:
            if (
                item["source_id"] is not None
                and item["source_id"] not in admitted_sources
            ):
                raise RuntimeError("dependency coverage references an omitted source")
            if (
                item["member_id"] is not None
                and item["member_id"] not in admitted_members
            ):
                raise RuntimeError("dependency coverage references an omitted member")


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
    archive_result: dict[str, object],
    absolute_path_observations: list[dict[str, object]],
    absolute_path_limit_candidate: dict[str, object] | None,
    absolute_path_scan_limited_source: dict[str, object] | None,
    parser_versions: Mapping[str, str] | None = None,
    dependency_imports: Sequence[ImportCandidate] = (),
    dependency_declarations: Sequence[DeclarationCandidate] = (),
    dependency_unsupported: Sequence[UnsupportedDependencyContent] = (),
    dependency_files: Sequence[VirtualFile] = (),
    dependency_source_locations: Mapping[str, VirtualLocation] | None = None,
    dependency_scan_limited_source: dict[str, object] | None = None,
    records: _BoundedRecords | None = None,
) -> dict[str, object]:
    """Build one schema-shaped report for completed snapshot intake."""

    bounded = records or _BoundedRecords()
    path_result = absolute_path_result(
        detected_kind,
        members,
        issues,
        source_index,
        absolute_path_observations,
        bounded,
        absolute_path_scan_limited_source,
    )
    evidence_start = sum(
        len(result["evidence"]) for result in (archive_result, path_result)
    )
    dependency, dependency_limit_candidate = dependency_result(
        detected_kind,
        members,
        issues,
        source_index,
        dependency_imports,
        dependency_declarations,
        dependency_unsupported,
        dependency_files,
        dependency_source_locations or {},
        bounded,
        dependency_scan_limited_source,
        evidence_start=evidence_start,
    )
    rule_results = [archive_result, path_result, dependency]
    if bounded.limit_name is not None:
        limit = bounded.limit_name
        if limit not in reached_limits:
            reached_limits.append(limit)
        inventory_status = "partial"
        member_by_id = {str(member["member_id"]): member for member in members}
        member = (
            None
            if bounded.blocked_member_id is None
            else member_by_id.get(bounded.blocked_member_id)
        )
        issue_code, issue_message = _TERMINAL_LIMIT_ISSUES[limit]
        if not any(issue["code"] == issue_code for issue in issues):
            bounded.append_reserved(
                issues,
                {
                    "code": issue_code,
                    "message": issue_message,
                    "member_id": None if member is None else member["member_id"],
                },
            )
        if bounded.blocked_rule_id == "python.absolute-path":
            observation, coverage = _path_limit_records(
                limit,
                absolute_path_limit_candidate,
                absolute_path_scan_limited_source,
                {str(source["source_id"]) for source in source_index},
                set(member_by_id),
            )
            bounded.append_reserved(path_result["observations"], observation)
            bounded.append_reserved(path_result["skipped_inputs"], coverage)
            path_result["status"] = "partial"
        elif bounded.blocked_rule_id == "python.dependencies":
            observation, coverage = _dependency_limit_records(
                limit,
                dependency_limit_candidate,
                {str(source["source_id"]) for source in source_index},
                set(member_by_id),
            )
            bounded.append_reserved(dependency["observations"], observation)
            bounded.append_reserved(dependency["skipped_inputs"], coverage)
            dependency["status"] = "partial"
        else:
            observation, coverage = archive_limit_records(limit, member)
            bounded.append_reserved(archive_result["observations"], observation)
            bounded.append_reserved(archive_result["skipped_inputs"], coverage)
            archive_result["status"] = "partial"
            sort_archive_result(archive_result)
    issues.sort(key=_issue_key)
    _assign_observation_ids(rule_results)
    _assert_dependency_references(rule_results, source_index, members)
    limit_order = {name: index for index, name in enumerate(FIXED_LIMITS)}
    reached_limits.sort(key=limit_order.__getitem__)
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
        "runtime": runtime_record(parsers, parser_versions),
        "limits": {"effective": dict(FIXED_LIMITS), "reached": reached_limits},
        "inventory": {
            "status": inventory_status,
            "members": members,
            "issues": issues,
        },
        "source_index": source_index,
        "rule_results": rule_results,
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

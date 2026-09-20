"""Validated loading for saved checker reports."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .checker_report import (
    RULE_IDS,
    RULESET_VERSION,
    SCHEMA_VERSION,
    _assert_report_references,
)

MAX_SAVED_REPORT_BYTES = 128 * 1024**2


class SavedReportError(ValueError):
    """A saved report cannot be admitted for viewing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SavedReportEntry:
    """One selected saved-report path or collection-selection error."""

    path: Path
    error: SavedReportError | None = None


def _absolute_path(path: Path) -> Path:
    return path.expanduser().absolute()


def _directory_candidates(
    directory: Path,
) -> tuple[list[Path], SavedReportError | None]:
    try:
        children = list(directory.iterdir())
    except OSError:
        return [], SavedReportError(
            "report_directory_unreadable",
            "The saved report directory could not be read.",
        )

    candidates = [child for child in children if child.suffix.casefold() == ".json"]
    for child in children:
        try:
            child_status = child.lstat()
        except OSError:
            continue
        if not stat.S_ISDIR(child_status.st_mode):
            continue
        report_path = child / "report.json"
        try:
            report_path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            pass
        candidates.append(report_path)

    candidates.sort(key=lambda candidate: candidate.relative_to(directory).as_posix())
    if candidates:
        return candidates, None
    return [], SavedReportError(
        "no_reports_found",
        "No saved report files were found in the selected directory.",
    )


def collect_saved_reports(paths: Sequence[Path]) -> list[SavedReportEntry]:
    """Expand explicit reports and shallow report directories deterministically."""

    entries: list[SavedReportEntry] = []
    seen_inputs: set[Path] = set()
    seen_entries: set[Path] = set()
    for supplied in paths:
        path = _absolute_path(supplied)
        if path in seen_inputs:
            continue
        seen_inputs.add(path)

        try:
            status = path.lstat()
        except OSError:
            status = None
        if status is None or not stat.S_ISDIR(status.st_mode):
            if path not in seen_entries:
                entries.append(SavedReportEntry(path))
                seen_entries.add(path)
            continue

        candidates, error = _directory_candidates(path)
        if error is not None:
            if path not in seen_entries:
                entries.append(SavedReportEntry(path, error))
                seen_entries.add(path)
            continue
        for candidate in candidates:
            normalized = _absolute_path(candidate)
            if normalized in seen_entries:
                continue
            entries.append(SavedReportEntry(normalized))
            seen_entries.add(normalized)
    return entries


def _read_regular_file(path: Path) -> bytes:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise SavedReportError(
            "report_not_found", "The saved report does not exist."
        ) from error
    except OSError as error:
        raise SavedReportError(
            "report_unreadable", "The saved report could not be read."
        ) from error

    if stat.S_ISLNK(before.st_mode):
        raise SavedReportError(
            "invalid_report_path",
            "The saved report must be a regular file, not a symbolic link.",
        )
    if not stat.S_ISREG(before.st_mode):
        raise SavedReportError(
            "invalid_report_path", "The saved report must be a regular file."
        )
    if before.st_size > MAX_SAVED_REPORT_BYTES:
        raise SavedReportError(
            "report_too_large", "The saved report exceeds the 128 MiB input limit."
        )

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SavedReportError(
            "report_unreadable", "The saved report could not be read."
        ) from error

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise SavedReportError(
                "invalid_report_path",
                "The saved report must remain one regular file while it is read.",
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(MAX_SAVED_REPORT_BYTES + 1)
    except SavedReportError:
        raise
    except OSError as error:
        raise SavedReportError(
            "report_unreadable", "The saved report could not be read."
        ) from error
    finally:
        os.close(descriptor)

    if len(payload) > MAX_SAVED_REPORT_BYTES:
        raise SavedReportError(
            "report_too_large", "The saved report exceeds the 128 MiB input limit."
        )
    return payload


def _reject_constant(_value: str) -> None:
    raise ValueError("nonstandard numeric constant")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _decode_document(payload: bytes) -> dict[str, object]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SavedReportError(
            "invalid_report_encoding", "The saved report is not valid UTF-8."
        ) from error
    try:
        document = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_object,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise SavedReportError(
            "invalid_report_json", "The saved report is not one valid JSON document."
        ) from error
    if not isinstance(document, dict):
        raise SavedReportError(
            "unsupported_report", "The saved JSON is not a checker report."
        )
    return document


def _require_supported_contract(document: dict[str, object]) -> None:
    identifying_fields = {"schema_version", "ruleset_version", "rule_results"}
    if not identifying_fields <= document.keys():
        raise SavedReportError(
            "unsupported_report", "The saved JSON is not a checker report."
        )
    if document["schema_version"] != SCHEMA_VERSION:
        raise SavedReportError(
            "unsupported_schema_version",
            "The saved checker report uses an unsupported schema version.",
        )
    if document["ruleset_version"] != RULESET_VERSION:
        raise SavedReportError(
            "unsupported_ruleset_version",
            "The saved checker report uses an unsupported ruleset version.",
        )

    results = document["rule_results"]
    if isinstance(results, list):
        known_rules = set(RULE_IDS)
        for result in results:
            if not isinstance(result, dict):
                continue
            rule_id = result.get("rule_id")
            if isinstance(rule_id, str) and rule_id not in known_rules:
                raise SavedReportError(
                    "unsupported_rule_id",
                    "The saved checker report contains an unsupported rule identifier.",
                )


@cache
def _validator() -> Draft202012Validator:
    schema_resource = resources.files("reproready").joinpath(
        "schemas/check-report-v1.schema.json"
    )
    schema = json.loads(schema_resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_saved_report(path: Path) -> dict[str, object]:
    """Load and validate one saved checker report without inspecting its artifact."""

    document = _decode_document(_read_regular_file(path))
    _require_supported_contract(document)
    try:
        _validator().validate(document)
    except ValidationError as error:
        raise SavedReportError(
            "invalid_report_schema",
            "The saved checker report does not match schema version 1.",
        ) from error

    inventory = document["inventory"]
    assert isinstance(inventory, dict)
    try:
        _assert_report_references(
            document["rule_results"],
            document["source_index"],
            inventory["members"],
            inventory["issues"],
        )
    except RuntimeError as error:
        raise SavedReportError(
            "invalid_report_references",
            "The saved checker report contains inconsistent references.",
        ) from error
    return document

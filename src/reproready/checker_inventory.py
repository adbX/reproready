"""Worker-side direct-file and recursive ZIP intake for the checker."""

from __future__ import annotations

import codecs
import os
import re
import stat
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on Python 3.10.
    import tomli as tomllib

from .checker_archive import archive_structure_result
from .checker_intake import FIXED_LIMITS, SourceSnapshot, WorkerCheckpoint
from .checker_python import (
    NotebookCellSourceError,
    NotebookParseError,
    has_unsupported_notebook_syntax,
    notebook_cell_source,
    parse_notebook_document,
    parse_python_source,
)
from .checker_python_dependencies import (
    PACKAGING_VERSION,
    DeclarationCandidate,
    DeclarationEvent,
    ImportCandidate,
    UnsupportedDependencyContent,
    VirtualFile,
    VirtualLocation,
    collect_import_candidates,
    minimum_reduced_candidate_size,
    reduce_pyproject_document,
    reduce_requirements_source,
    split_virtual_name,
    unsupported_dependency_form,
    validate_pyproject_document,
)
from .checker_python_paths import (
    AbsolutePathCandidate,
    collect_absolute_path_observations,
)
from .checker_python_review import ReviewCollector
from .checker_report import (
    _BoundedRecords,
    classify_snapshot,
    encode_report,
    successful_intake_report,
)

_READ_CHUNK_BYTES = 1024 * 1024
_SOURCE_FORMS = {
    "direct_python": "python_file",
    "direct_notebook": "notebook_document",
    "direct_requirements": "requirements",
    "direct_pyproject": "pyproject",
}
_SOURCE_LIMITS = {
    "python_file": "max_python_source_bytes",
    "notebook_document": "max_notebook_bytes",
    "requirements": "max_dependency_file_bytes",
    "pyproject": "max_dependency_file_bytes",
}
_UNREADABLE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".gz",
    ".pdf",
    ".docx",
)
_REQUIREMENTS_NAME = re.compile(r"requirements.*\.txt", re.IGNORECASE)
_BIDI_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}
_LIMIT_ISSUES = {
    "max_member_count": (
        "member_count_limit",
        "The archive contains more entries than the checker can inspect.",
    ),
    "max_nested_zip_depth": (
        "nested_zip_depth_limit",
        "At least one nested ZIP was inventoried but not opened because it exceeds depth 3.",
    ),
    "max_expanded_bytes_per_member": (
        "expanded_member_limit",
        "An expanded archive entry exceeded the per-entry size limit.",
    ),
    "max_expanded_bytes_total": (
        "expanded_total_limit",
        "The total expanded archive data exceeded the inspection limit.",
    ),
    "max_python_source_bytes": (
        "python_source_limit",
        "A Python source exceeded the size limit for analysis.",
    ),
    "max_notebook_bytes": (
        "notebook_limit",
        "A notebook exceeded the size limit for analysis.",
    ),
    "max_dependency_file_bytes": (
        "dependency_file_limit",
        "A dependency file exceeded the size limit for analysis.",
    ),
    "max_temporary_bytes": (
        "temporary_storage_limit",
        "The checker exhausted its bounded temporary storage.",
    ),
    "max_report_bytes": (
        "report_size_limit",
        "The JSON report exceeded its size limit.",
    ),
}
_SOURCE_READ_BLOCKERS = (
    ("symlink_entry", "skipped"),
    ("special_entry", "skipped"),
    ("encrypted_entry", "skipped"),
    ("unsupported_compression", "skipped"),
    ("expanded_member_limit", "skipped"),
    ("expanded_total_limit", "skipped"),
    ("temporary_storage_limit", "skipped"),
    ("integrity_error", "error"),
    ("checker_cache_error", "error"),
    ("member_read_error", "error"),
)


class InventoryError(RuntimeError):
    """A worker-side failure with a stable issue code and no artifact text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class _Container:
    container_id: str
    path: Path
    parent_member_id: str | None
    depth: int


@dataclass(slots=True)
class _Target:
    target_id: str
    member_id: str | None
    container_id: str | None
    parent_member_id: str | None
    name: str
    duplicate_ordinal: int | None
    kind: str
    compressed_size: int | None
    expanded_size: int | None
    member: dict[str, object] | None
    container: _Container | None = None
    zip_info: zipfile.ZipInfo | None = None
    cache_path: Path | None = None
    text_valid: bool | None = None


@dataclass(frozen=True, slots=True)
class _SourceDescriptor:
    target: _Target
    form: str


def _member_basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _member_source_form(name: str) -> str | None:
    basename = _member_basename(name)
    folded = basename.casefold()
    if unsupported_dependency_form(name):
        return "unsupported_source"
    if folded.endswith(".py"):
        return "python_file"
    if folded.endswith(".ipynb"):
        return "notebook_document"
    if _REQUIREMENTS_NAME.fullmatch(basename):
        return "requirements"
    if folded == "pyproject.toml":
        return "pyproject"
    return None


def _member_kind(info: zipfile.ZipInfo) -> str:
    if info.is_dir():
        return "directory"
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        return "symlink"
    if file_type not in {0, stat.S_IFREG}:
        return "special"
    return "file"


def _path_issues(name: str) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    normalized = name.replace("\\", "/")
    if name.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        issues.append(
            (
                "absolute_member_path",
                "The decoded member name has Windows device-path syntax.",
            )
        )
    elif name.startswith(("\\\\", "//")):
        issues.append(
            (
                "absolute_member_path",
                "The decoded member name has Windows UNC-path syntax.",
            )
        )
    elif re.match(r"^[A-Za-z]:[/\\]", name):
        issues.append(
            (
                "absolute_member_path",
                "The decoded member name has Windows drive-root syntax.",
            )
        )
    elif name.startswith("/"):
        issues.append(
            (
                "absolute_member_path",
                "The decoded member name has POSIX absolute-path syntax.",
            )
        )
    if ".." in normalized.split("/"):
        issues.append(
            (
                "parent_path_segment",
                "The decoded member name contains a parent-path segment.",
            )
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        issues.append(
            (
                "control_character_in_name",
                "The decoded member name contains a control character.",
            )
        )
    return issues


def _issue(code: str, message: str, member_id: str | None) -> dict[str, object]:
    return {"code": code, "message": message, "member_id": member_id}


def _remove_cache_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _source_record(
    source_id: str,
    member_id: str | None,
    form: str,
    status: str,
    *,
    cell: int | None = None,
    language: str | None = None,
    reason_code: str | None = None,
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "member_id": member_id,
        "form": form,
        "status": status,
        "cell": cell,
        "language": language,
        "reason_code": reason_code,
    }


def _display_text(value: str) -> str:
    output: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "[":
            output.append("\\[")
        elif (
            codepoint < 32
            or codepoint == 127
            or codepoint in _BIDI_CODEPOINTS
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            output.append(f"\\u{codepoint:04x}")
        else:
            output.append(character)
    return "".join(output)


def _bounded_display(value: str) -> tuple[str, bool]:
    escaped = _display_text(value)
    limit = 240
    if len(escaped) <= limit:
        return escaped, False
    return escaped[:limit], True


class InspectionEngine:
    """Own one completed snapshot, its inventory, and cumulative read budgets."""

    def __init__(
        self,
        snapshot: SourceSnapshot,
        checkpoint: WorkerCheckpoint,
        cache_root: Path,
    ) -> None:
        self.snapshot = snapshot
        self.checkpoint = checkpoint
        self.cache_root = cache_root
        self.detected_kind = classify_snapshot(snapshot.display_name, snapshot.path)
        self.parsers: set[str] = set()
        self.reached_limits: list[str] = []
        self.records = _BoundedRecords()
        self._limit_members: dict[str, str | None] = {}
        self.inventory_issues: list[dict[str, object]] = []
        self.members: list[dict[str, object]] = []
        self.sources: list[dict[str, object]] = []
        self._source_descriptors: list[_SourceDescriptor] = []
        self.absolute_path_observations: list[dict[str, object]] = []
        self.absolute_path_limit_candidate: AbsolutePathCandidate | None = None
        self.absolute_path_scan_limited_source: dict[str, object] | None = None
        self.dependency_imports: list[ImportCandidate] = []
        self.dependency_declarations: list[DeclarationCandidate] = []
        self.dependency_unsupported: dict[
            tuple[str, str], UnsupportedDependencyContent
        ] = {}
        self.dependency_source_locations: dict[str, VirtualLocation] = {}
        self.dependency_candidate_bytes = 0
        self.dependency_scan_limited_source: dict[str, object] | None = None
        self.parser_versions: dict[str, str] = {}
        self.python_review = ReviewCollector()
        self.targets: list[_Target] = []
        self.target_by_id: dict[str, _Target] = {}
        self.containers: list[_Container] = []
        self.expanded_bytes = 0
        self.temporary_bytes = snapshot.size_bytes
        self.inventory_status = "complete"
        self._stop_inventory = False
        self._cursor_number = 0
        self._cursors: dict[str, tuple[Any, ...]] = {}

    def _record_limit_stop(self) -> None:
        if self.records.limit_name != "max_report_bytes":
            return
        if "max_report_bytes" not in self.reached_limits:
            self.reached_limits.append("max_report_bytes")
        self.inventory_status = "partial"
        self._stop_inventory = True

    def _append_inventory_issue(self, issue: dict[str, object]) -> bool:
        accepted = self.records.append(
            self.inventory_issues,
            issue,
            member_id=issue["member_id"],
        )
        if not accepted:
            self._record_limit_stop()
        return accepted

    def _append_member(self, member: dict[str, object]) -> bool:
        accepted = self.records.append(self.members, member)
        if not accepted:
            self._record_limit_stop()
        return accepted

    def _append_source(self, source: dict[str, object]) -> bool:
        accepted = self.records.append(
            self.sources,
            source,
            member_id=source["member_id"],
            source_id=source["source_id"],
        )
        if not accepted:
            self._record_limit_stop()
            return False
        member_id = source["member_id"]
        target = (
            self.target_by_id.get("artifact")
            if member_id is None
            else self.target_by_id.get(str(member_id))
        )
        if target is not None:
            source_id = str(source["source_id"])
            self.dependency_source_locations[source_id] = VirtualLocation(
                target.container_id or "direct:0",
                split_virtual_name(target.name),
                None if member_id is None else str(member_id),
                source_id,
            )
        return True

    def _add_dependency_unsupported(self, item: UnsupportedDependencyContent) -> None:
        self.dependency_unsupported.setdefault(
            (item.source_id, item.reason_code),
            item,
        )

    def _retain_dependency_candidate(
        self, candidate: ImportCandidate | DeclarationCandidate
    ) -> bool:
        size = minimum_reduced_candidate_size(candidate)
        if self.dependency_candidate_bytes + size > FIXED_LIMITS["max_report_bytes"]:
            if self.dependency_scan_limited_source is None:
                self.dependency_scan_limited_source = {
                    "member_id": candidate.location.member_id,
                    "source_id": candidate.location.source_id,
                    "line": candidate.line,
                    "cell": (
                        candidate.cell
                        if isinstance(candidate, ImportCandidate)
                        else None
                    ),
                }
            return False
        self.dependency_candidate_bytes += size
        if isinstance(candidate, ImportCandidate):
            self.dependency_imports.append(candidate)
        else:
            self.dependency_declarations.append(candidate)
        return True

    def _dependency_files(self) -> list[VirtualFile]:
        source_by_member = {
            str(source["member_id"]): str(source["source_id"])
            for source in self.sources
            if source["member_id"] is not None
            and source["form"] == "python_file"
            and source["cell"] is None
        }
        files = [
            VirtualFile(
                str(member["container_id"]),
                split_virtual_name(str(member["name"])),
                str(member["name"]),
                str(member["member_id"]),
                source_by_member.get(str(member["member_id"])),
                str(member["kind"]),
                ordinal,
            )
            for ordinal, member in enumerate(self.members)
        ]
        direct_source = next(
            (
                source
                for source in self.sources
                if source["member_id"] is None and source["form"] == "python_file"
            ),
            None,
        )
        direct_target = self.target_by_id.get("artifact")
        if direct_source is not None and direct_target is not None:
            files.append(
                VirtualFile(
                    "direct:0",
                    split_virtual_name(direct_target.name),
                    direct_target.name,
                    None,
                    str(direct_source["source_id"]),
                    direct_target.kind,
                    0,
                )
            )
        return files

    def _append_reached_issues(self) -> None:
        member_ids = {str(member["member_id"]) for member in self.members}
        existing_codes = {str(issue["code"]) for issue in self.inventory_issues}
        for limit in tuple(self.reached_limits):
            if limit not in _LIMIT_ISSUES:
                continue
            code, message = _LIMIT_ISSUES[limit]
            if code in existing_codes:
                continue
            member_id = self._limit_members.get(limit)
            if member_id not in member_ids:
                member_id = None
            if not self._append_inventory_issue(_issue(code, message, member_id)):
                return
            existing_codes.add(code)

    def inspect(self) -> dict[str, object]:
        """Build bounded direct or ZIP intake and return its report object."""

        self.checkpoint("after_source_read")
        inspect_sources = False
        if self.detected_kind in _SOURCE_FORMS:
            self._inspect_direct()
            inspect_sources = True
        elif self.detected_kind == "zip":
            self.parsers.add("zipfile")
            self._inspect_zip()
            inspect_sources = True
        else:
            self.inventory_status = "unsupported"
            syntax = self.detected_kind.removeprefix("unsupported_").replace("_", " ")
            self._append_inventory_issue(
                _issue(
                    "unsupported_format",
                    f"The regular file has {syntax} syntax, which is unsupported in v1.",
                    None,
                )
            )
            self._add_target(
                _Target(
                    "artifact",
                    None,
                    None,
                    None,
                    self.snapshot.display_name,
                    None,
                    "unknown",
                    None,
                    self.snapshot.size_bytes,
                    None,
                    cache_path=self.snapshot.path,
                )
            )
        self._append_reached_issues()
        archive_result = archive_structure_result(
            self.detected_kind,
            self.members,
            self.inventory_issues,
            self.reached_limits,
            append_record=self.records.append,
        )
        if inspect_sources:
            self._inspect_sources()
        self._append_reached_issues()
        report = successful_intake_report(
            self.snapshot,
            detected_kind=self.detected_kind,
            parsers=self.parsers,
            parser_versions=self.parser_versions,
            reached_limits=self.reached_limits,
            inventory_status=self.inventory_status,
            members=self.members,
            issues=self.inventory_issues,
            source_index=self.sources,
            archive_result=archive_result,
            absolute_path_observations=self.absolute_path_observations,
            absolute_path_limit_candidate=(
                None
                if self.absolute_path_limit_candidate is None
                else self.absolute_path_limit_candidate.observation
            ),
            absolute_path_scan_limited_source=self.absolute_path_scan_limited_source,
            dependency_imports=self.dependency_imports,
            dependency_declarations=self.dependency_declarations,
            dependency_unsupported=list(self.dependency_unsupported.values()),
            dependency_files=self._dependency_files(),
            dependency_source_locations=self.dependency_source_locations,
            dependency_scan_limited_source=self.dependency_scan_limited_source,
            python_review=self.python_review,
            records=self.records,
        )
        if len(encode_report(report)) > FIXED_LIMITS["max_report_bytes"]:
            raise InventoryError(
                "max_report_bytes",
                "The inventory could not fit within the JSON report size limit.",
            )
        return report

    def _inspect_direct(self) -> None:
        form = _SOURCE_FORMS[self.detected_kind]
        target = _Target(
            "artifact",
            None,
            None,
            None,
            self.snapshot.display_name,
            None,
            "file",
            None,
            self.snapshot.size_bytes,
            None,
            cache_path=self.snapshot.path,
        )
        self._add_target(target)
        self._source_descriptors.append(_SourceDescriptor(target, form))
        if self.reached_limits:
            self.inventory_status = "partial"

    def _inspect_zip(self) -> None:
        outer = _Container("container:0", self.snapshot.path, None, 0)
        self.containers.append(outer)
        try:
            self._walk_container(outer)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
            self._append_inventory_issue(
                _issue(
                    "central_directory_error",
                    "The ZIP central directory could not be read completely.",
                    outer.parent_member_id,
                )
            )
            self.inventory_status = "partial"
        if self.reached_limits and self.inventory_status == "complete":
            self.inventory_status = "partial"

    def _walk_container(self, container: _Container) -> None:
        with zipfile.ZipFile(container.path) as archive:
            infos = archive.infolist()
            duplicate_ordinals: Counter[str] = Counter()
            for info in infos:
                if self._stop_inventory:
                    return
                if len(self.members) >= FIXED_LIMITS["max_member_count"]:
                    self._reach("max_member_count", None)
                    self._stop_inventory = True
                    return
                duplicate_ordinals[info.filename] += 1
                duplicate_ordinal = duplicate_ordinals[info.filename]
                member_id = f"member:{len(self.members)}"
                issues = [
                    _issue(code, message, member_id)
                    for code, message in _path_issues(info.filename)
                ]
                if duplicate_ordinal > 1:
                    issues.append(
                        _issue(
                            "duplicate_member_name",
                            "The decoded member name repeats an earlier entry in this container.",
                            member_id,
                        )
                    )
                kind = _member_kind(info)
                if kind == "symlink":
                    issues.append(
                        _issue(
                            "symlink_entry",
                            "The archive entry is a symbolic link and was not followed.",
                            member_id,
                        )
                    )
                elif kind == "special":
                    issues.append(
                        _issue(
                            "special_entry",
                            "The archive entry is neither a regular file nor a directory.",
                            member_id,
                        )
                    )
                if info.flag_bits & 1:
                    issues.append(
                        _issue(
                            "encrypted_entry",
                            "The encrypted archive entry was not read.",
                            member_id,
                        )
                    )
                member = {
                    "container_id": container.container_id,
                    "member_id": member_id,
                    "parent_member_id": container.parent_member_id,
                    "name": info.filename,
                    "duplicate_ordinal": duplicate_ordinal,
                    "kind": kind,
                    "compressed_size": info.compress_size,
                    "expanded_size": info.file_size,
                    "read_status": (
                        "unsupported"
                        if kind in {"symlink", "special"} or info.flag_bits & 1
                        else "not_read"
                    ),
                    "integrity_status": "not_checked",
                    "issues": issues,
                }
                target = _Target(
                    member_id,
                    member_id,
                    container.container_id,
                    container.parent_member_id,
                    info.filename,
                    duplicate_ordinal,
                    kind,
                    info.compress_size,
                    info.file_size,
                    member,
                    container=container,
                    zip_info=info,
                )
                folded = _member_basename(info.filename).casefold()
                form = _member_source_form(info.filename)
                nested_candidate = kind == "file" and folded.endswith(".zip")
                if nested_candidate and (
                    container.depth >= FIXED_LIMITS["max_nested_zip_depth"]
                ):
                    member["read_status"] = "unsupported"
                    member["issues"].append(
                        _issue(
                            "nested_zip_depth_limit",
                            "The nested ZIP was inventoried but not opened beyond depth 3.",
                            member_id,
                        )
                    )
                    self._reach("max_nested_zip_depth", member_id)
                elif (
                    kind == "file"
                    and not info.flag_bits & 1
                    and form != "unsupported_source"
                ):
                    retain = nested_candidate or (
                        form is not None
                        and info.file_size <= FIXED_LIMITS[_SOURCE_LIMITS[form]]
                    )
                    self._read_target(target, archive=archive, retain=retain)
                if not self._append_member(member):
                    return
                self._add_target(target)
                if form is not None:
                    self._source_descriptors.append(_SourceDescriptor(target, form))
                if nested_candidate and target.cache_path is not None:
                    self._open_nested_zip(target, container.depth)

    def _open_nested_zip(self, target: _Target, parent_depth: int) -> None:
        cache = target.cache_path
        assert cache is not None
        if not zipfile.is_zipfile(cache):
            return
        assert target.member is not None
        target.kind = "zip"
        target.member["kind"] = "zip"
        container = _Container(
            f"container:{len(self.containers)}",
            cache,
            target.member_id,
            parent_depth + 1,
        )
        self.containers.append(container)
        try:
            self._walk_container(container)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
            self._append_inventory_issue(
                _issue(
                    "central_directory_error",
                    "A nested ZIP central directory could not be read completely.",
                    target.member_id,
                )
            )
            self.inventory_status = "partial"

    def _cache_target(self, target: _Target) -> Path | None:
        if target.cache_path is not None:
            return target.cache_path
        return self._read_target(target, retain=True)

    def _read_target(
        self,
        target: _Target,
        *,
        archive: zipfile.ZipFile | None = None,
        retain: bool,
    ) -> Path | None:
        assert target.member is not None
        assert target.container is not None and target.zip_info is not None
        expected = target.expanded_size
        if expected is not None:
            if expected > FIXED_LIMITS["max_expanded_bytes_per_member"]:
                self._member_limit(target, "max_expanded_bytes_per_member")
                return None
            if (
                self.expanded_bytes + expected
                > FIXED_LIMITS["max_expanded_bytes_total"]
            ):
                self._member_limit(target, "max_expanded_bytes_total")
                return None
            if (
                retain
                and self.temporary_bytes + expected
                > FIXED_LIMITS["max_temporary_bytes"]
            ):
                self._member_limit(target, "max_temporary_bytes")
                return None

        destination = (
            self.cache_root / f"member-{target.member_id.split(':')[1]}.data"
            if retain
            else None
        )
        partial = (
            destination.with_suffix(".partial") if destination is not None else None
        )
        output = None
        written = 0
        cache_error = False

        if partial is not None:
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    partial,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                    stat.S_IRUSR | stat.S_IWUSR,
                )
                output = os.fdopen(descriptor, "wb")
                descriptor = None
            except OSError:
                cache_error = True
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

        def read_from(active_archive: zipfile.ZipFile) -> bool:
            nonlocal cache_error, output, written
            with active_archive.open(target.zip_info) as source:
                member_bytes = 0
                while chunk := source.read(_READ_CHUNK_BYTES):
                    if (
                        member_bytes + len(chunk)
                        > FIXED_LIMITS["max_expanded_bytes_per_member"]
                    ):
                        self._member_limit(target, "max_expanded_bytes_per_member")
                        return False
                    if (
                        self.expanded_bytes + len(chunk)
                        > FIXED_LIMITS["max_expanded_bytes_total"]
                    ):
                        self._member_limit(target, "max_expanded_bytes_total")
                        return False
                    member_bytes += len(chunk)
                    self.expanded_bytes += len(chunk)
                    if output is None:
                        continue
                    if (
                        self.temporary_bytes + len(chunk)
                        > FIXED_LIMITS["max_temporary_bytes"]
                    ):
                        self._member_limit(target, "max_temporary_bytes")
                        return False
                    try:
                        output.write(chunk)
                    except OSError:
                        cache_error = True
                        self.temporary_bytes -= written
                        written = 0
                        try:
                            output.close()
                        except OSError:
                            pass
                        output = None
                        if partial is not None:
                            _remove_cache_file(partial)
                    else:
                        written += len(chunk)
                        self.temporary_bytes += len(chunk)
            return True

        complete = False
        try:
            if archive is None:
                with zipfile.ZipFile(target.container.path) as reopened:
                    complete = read_from(reopened)
            else:
                complete = read_from(archive)
        except NotImplementedError:
            target.member["read_status"] = "unsupported"
            target.member["issues"].append(
                _issue(
                    "unsupported_compression",
                    "The archive entry uses an unsupported compression method.",
                    target.member_id,
                )
            )
        except (RuntimeError, zipfile.BadZipFile, EOFError):
            target.member["read_status"] = "error"
            target.member["integrity_status"] = "error"
            target.member["issues"].append(
                _issue(
                    "integrity_error",
                    "The archive entry could not be read and verified completely.",
                    target.member_id,
                )
            )
        except OSError:
            target.member["read_status"] = "error"
            target.member["issues"].append(
                _issue(
                    "member_read_error",
                    "The archive entry could not be read because of a checker I/O failure.",
                    target.member_id,
                )
            )
        finally:
            if output is not None:
                try:
                    output.close()
                except OSError:
                    cache_error = True
                output = None
            if not complete or cache_error:
                self.temporary_bytes -= written
                written = 0
                if partial is not None:
                    _remove_cache_file(partial)

        if not complete:
            return None
        target.member["read_status"] = "complete"
        target.member["integrity_status"] = "ok"
        if destination is None:
            return None
        if cache_error or partial is None:
            target.member["issues"].append(
                _issue(
                    "checker_cache_error",
                    "The verified archive entry could not be retained in checker storage.",
                    target.member_id,
                )
            )
            return None
        try:
            os.replace(partial, destination)
            destination.chmod(stat.S_IRUSR)
        except OSError:
            self.temporary_bytes -= written
            _remove_cache_file(partial)
            _remove_cache_file(destination)
            target.member["issues"].append(
                _issue(
                    "checker_cache_error",
                    "The verified archive entry could not be retained in checker storage.",
                    target.member_id,
                )
            )
            return None
        target.cache_path = destination
        return destination

    def _member_limit(self, target: _Target, limit: str) -> None:
        assert target.member is not None
        code, message = _LIMIT_ISSUES[limit]
        target.member["read_status"] = "partial"
        target.member["issues"].append(_issue(code, message, target.member_id))
        self._reach(limit, target.member_id)

    def _reach(self, limit: str, member_id: str | None) -> None:
        if limit not in self.reached_limits:
            self.reached_limits.append(limit)
            self._limit_members[limit] = member_id
        self.inventory_status = "partial"

    def _inspect_sources(self) -> None:
        descriptors = self._source_descriptors
        self._source_descriptors = []
        for index, descriptor in enumerate(descriptors):
            if self.records.limit_name == "max_report_bytes":
                self._mark_path_scan_limited(descriptors[index:])
                self._mark_dependency_scan_limited(descriptors[index:])
                return
            self._inspect_source(descriptor)
            if self.records.limit_name == "max_report_bytes":
                self._mark_path_scan_limited(descriptors[index:])
                self._mark_dependency_scan_limited(descriptors[index:])
                return

    def _mark_path_scan_limited(
        self,
        descriptors: list[_SourceDescriptor],
    ) -> None:
        if self.absolute_path_scan_limited_source is not None:
            return
        for descriptor in descriptors:
            if descriptor.form not in {"python_file", "notebook_document"}:
                continue
            self.absolute_path_scan_limited_source = {
                "member_id": descriptor.target.member_id,
                "source_id": None,
            }
            return

    def _mark_dependency_scan_limited(
        self,
        descriptors: list[_SourceDescriptor],
    ) -> None:
        if self.dependency_scan_limited_source is not None:
            return
        applicable = {
            "python_file",
            "notebook_document",
            "requirements",
            "pyproject",
            "unsupported_source",
        }
        for descriptor in descriptors:
            if descriptor.form not in applicable:
                continue
            self.dependency_scan_limited_source = {
                "member_id": descriptor.target.member_id,
                "source_id": None,
            }
            return

    def _inspect_source(self, descriptor: _SourceDescriptor) -> None:
        target = descriptor.target
        form = descriptor.form
        source_id = f"source:{len(self.sources)}"
        language = "python" if form == "python_file" else None
        if form == "unsupported_source":
            self._append_source(
                _source_record(
                    source_id,
                    target.member_id,
                    form,
                    "unsupported",
                    reason_code="unsupported_dependency_form",
                )
            )
            return
        limit_name = _SOURCE_LIMITS[form]
        size = target.expanded_size
        if size is not None and size > FIXED_LIMITS[limit_name]:
            self._append_source(
                _source_record(
                    source_id,
                    target.member_id,
                    form,
                    "skipped",
                    language=language,
                    reason_code=limit_name,
                )
            )
            self._reach(limit_name, target.member_id)
            return
        path = target.cache_path
        if path is None:
            status, reason = self._target_blocker(target)
            self._append_source(
                _source_record(
                    source_id,
                    target.member_id,
                    form,
                    status,
                    language=language,
                    reason_code=reason,
                )
            )
            return
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            self._append_source(
                _source_record(
                    source_id,
                    target.member_id,
                    form,
                    "error",
                    language=language,
                    reason_code="decode_error",
                )
            )
            target.text_valid = False
            return
        except OSError:
            self._append_source(
                _source_record(
                    source_id,
                    target.member_id,
                    form,
                    "error",
                    language=language,
                    reason_code="member_read_error",
                )
            )
            return
        target.text_valid = True
        if form == "python_file":
            self._inspect_python(target, source_id, None, text)
        elif form == "notebook_document":
            self._inspect_notebook(target, source_id, text)
        elif form == "requirements":
            if not self._append_source(
                _source_record(source_id, target.member_id, form, "inspected")
            ):
                return
            location = self.dependency_source_locations[source_id]
            for event in reduce_requirements_source(location, text):
                self._accept_declaration_event(event)
        else:
            self.parsers.add("tomllib")
            try:
                document = tomllib.loads(text)
                validate_pyproject_document(document)
            except (tomllib.TOMLDecodeError, TypeError, AttributeError):
                self._append_source(
                    _source_record(
                        source_id,
                        target.member_id,
                        form,
                        "error",
                        reason_code="dependency_parse_error",
                    )
                )
                return
            if not self._append_source(
                _source_record(source_id, target.member_id, form, "inspected")
            ):
                return
            location = self.dependency_source_locations[source_id]
            for event in reduce_pyproject_document(location, document):
                self._accept_declaration_event(event)

    def _accept_declaration_event(self, event: DeclarationEvent) -> None:
        if event.parser_examined:
            self.parsers.add("packaging")
            self.parser_versions["packaging"] = PACKAGING_VERSION
        if event.unsupported is not None:
            self._add_dependency_unsupported(event.unsupported)
        if event.candidate is not None:
            self._retain_dependency_candidate(event.candidate)

    def _inspect_python(
        self,
        target: _Target,
        source_id: str,
        cell: int | None,
        source: str,
    ) -> None:
        if cell is None:
            self.parsers.add("tokenize")
            self.python_review.inspect_python_lexical(
                source_id,
                target.member_id,
                cell,
                source,
            )
        self.parsers.add("ast")
        try:
            parsed = parse_python_source(source_id, target.member_id, cell, source)
        except SyntaxError:
            self._append_source(
                _source_record(
                    source_id,
                    target.member_id,
                    "python_file" if cell is None else "notebook_code_cell",
                    "error",
                    cell=cell,
                    language="python",
                    reason_code="syntax_error",
                )
            )
            if cell is not None:
                self.python_review.invalidate_notebook(target.member_id)
            return
        if not self._append_source(
            _source_record(
                source_id,
                target.member_id,
                "python_file" if cell is None else "notebook_code_cell",
                "inspected",
                cell=cell,
                language="python",
            )
        ):
            return
        self.python_review.inspect_parsed(parsed)
        location = self.dependency_source_locations[source_id]
        for candidate in collect_import_candidates(parsed, location):
            if isinstance(candidate, UnsupportedDependencyContent):
                self._add_dependency_unsupported(candidate)
            else:
                self._retain_dependency_candidate(candidate)
        if self.absolute_path_scan_limited_source is not None:
            return
        if self.records.limit_name is not None:
            self.absolute_path_scan_limited_source = {
                "member_id": target.member_id,
                "source_id": source_id,
            }
            return
        for candidate in collect_absolute_path_observations(parsed):
            observation = candidate.observation
            if not self.records.append(
                self.absolute_path_observations,
                observation,
                member_id=target.member_id,
                source_id=source_id,
                observation=True,
            ):
                self.absolute_path_limit_candidate = candidate
                self.absolute_path_scan_limited_source = {
                    "member_id": target.member_id,
                    "source_id": source_id,
                }
                break

    def _inspect_notebook(
        self,
        target: _Target,
        document_id: str,
        text: str,
    ) -> None:
        self.parsers.add("json")
        try:
            document = parse_notebook_document(text)
        except NotebookParseError:
            self._append_source(
                _source_record(
                    document_id,
                    target.member_id,
                    "notebook_document",
                    "error",
                    reason_code="notebook_parse_error",
                )
            )
            self.python_review.notebook_document_gap(
                target.member_id,
                document_id,
                "notebook_parse_error",
            )
            return
        if type(document.get("nbformat")) is not int or document["nbformat"] != 4:
            self._append_source(
                _source_record(
                    document_id,
                    target.member_id,
                    "notebook_document",
                    "unsupported",
                    reason_code="unsupported_notebook_version",
                )
            )
            return
        languages = set()
        metadata = document.get("metadata")
        if isinstance(metadata, dict):
            kernelspec = metadata.get("kernelspec")
            language_info = metadata.get("language_info")
            if isinstance(kernelspec, dict) and isinstance(
                kernelspec.get("language"), str
            ):
                languages.add(kernelspec["language"].casefold())
            if isinstance(language_info, dict) and isinstance(
                language_info.get("name"), str
            ):
                languages.add(language_info["name"].casefold())
        if languages != {"python"}:
            language = next(iter(languages)) if len(languages) == 1 else None
            if language is not None and len(language) > 128:
                language = None
            self._append_source(
                _source_record(
                    document_id,
                    target.member_id,
                    "notebook_document",
                    "unsupported",
                    language=language,
                    reason_code="unsupported_notebook_language",
                )
            )
            return
        accepted = self._append_source(
            _source_record(
                document_id,
                target.member_id,
                "notebook_document",
                "inspected",
                language="python",
            )
        )
        if not accepted:
            return
        self.python_review.begin_notebook(target.member_id)
        cells = document["cells"]
        assert isinstance(cells, list)
        for cell_number, cell in enumerate(cells, start=1):
            if self.records.limit_name == "max_report_bytes":
                self.python_review.end_notebook(target.member_id)
                return
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            source_id = f"source:{len(self.sources)}"
            try:
                source = notebook_cell_source(cell)
                source_bytes = len(source.encode("utf-8"))
            except (NotebookCellSourceError, UnicodeEncodeError):
                self._append_source(
                    _source_record(
                        source_id,
                        target.member_id,
                        "notebook_code_cell",
                        "error",
                        cell=cell_number,
                        language="python",
                        reason_code="notebook_cell_source_error",
                    )
                )
                self.python_review.invalidate_notebook(target.member_id)
                continue
            if source_bytes > FIXED_LIMITS["max_python_source_bytes"]:
                self._append_source(
                    _source_record(
                        source_id,
                        target.member_id,
                        "notebook_code_cell",
                        "skipped",
                        cell=cell_number,
                        language="python",
                        reason_code="max_python_source_bytes",
                    )
                )
                self._reach("max_python_source_bytes", target.member_id)
                self.python_review.invalidate_notebook(target.member_id)
                continue
            self.parsers.add("tokenize")
            self.python_review.inspect_python_lexical(
                source_id,
                target.member_id,
                cell_number,
                source,
            )
            if self.python_review.inspect_notebook_shell(
                source_id,
                target.member_id,
                cell_number,
                source,
            ):
                self.parsers.add("shlex")
            if has_unsupported_notebook_syntax(source):
                self._append_source(
                    _source_record(
                        source_id,
                        target.member_id,
                        "notebook_code_cell",
                        "unsupported",
                        cell=cell_number,
                        language="python",
                        reason_code="unsupported_notebook_syntax",
                    )
                )
                self.python_review.invalidate_notebook(target.member_id)
                continue
            self._inspect_python(target, source_id, cell_number, source)
        self.python_review.end_notebook(target.member_id)

    def _target_blocker(self, target: _Target) -> tuple[str, str]:
        if target.member is None:
            return "error", "member_read_error"
        issue_codes = {str(issue["code"]) for issue in target.member["issues"]}
        for reason, status in _SOURCE_READ_BLOCKERS:
            if reason in issue_codes:
                return status, reason
        return "error", "member_read_error"

    def _add_target(self, target: _Target) -> None:
        self.targets.append(target)
        self.target_by_id[target.target_id] = target

    def target_listing(self, target: _Target) -> dict[str, object]:
        """Return bounded display metadata for one browser target."""

        name_display, name_truncated = _bounded_display(target.name)
        issue_codes = (
            [str(item["code"]) for item in target.member["issues"]]
            if target.member is not None
            else []
        )
        reason = self._structural_skip_reason(target)
        return {
            "target_id": target.target_id,
            "member_id": target.member_id,
            "container_id": target.container_id,
            "parent_member_id": target.parent_member_id,
            "name_display": name_display,
            "name_truncated": name_truncated,
            "duplicate_ordinal": target.duplicate_ordinal,
            "kind": target.kind,
            "compressed_size": target.compressed_size,
            "expanded_size": target.expanded_size,
            "issue_codes": issue_codes,
            "readable": reason is None,
            "searchable": reason is None,
        }

    def _structural_skip_reason(self, target: _Target) -> str | None:
        if target.member_id is None:
            if self.detected_kind not in _SOURCE_FORMS:
                return "unsupported_format"
            return None
        if target.kind == "directory":
            return "directory"
        if target.kind == "symlink":
            return "link"
        if target.kind == "special":
            return "special_entry"
        if target.kind == "zip":
            return "unsupported_format"
        assert target.member is not None
        codes = {str(issue["code"]) for issue in target.member["issues"]}
        if "encrypted_entry" in codes:
            return "encrypted"
        if "unsupported_compression" in codes:
            return "unsupported_compression"
        if "integrity_error" in codes or "member_read_error" in codes:
            return "member_read_error"
        if _member_basename(target.name).casefold().endswith(_UNREADABLE_SUFFIXES):
            return "unsupported_format"
        return None

    def ensure_text(self, target: _Target) -> tuple[Path | None, str | None]:
        """Return a strict-UTF-8 checker-owned path or one closed skip reason."""

        structural = self._structural_skip_reason(target)
        if structural is not None:
            return None, structural
        path = target.cache_path
        if path is None:
            path = self._cache_target(target)
        if path is None:
            return None, self._target_reason(target)
        if target.text_valid is False:
            return None, "decode_error"
        if target.text_valid is None:
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            try:
                with path.open("rb") as handle:
                    while chunk := handle.read(_READ_CHUNK_BYTES):
                        decoder.decode(chunk)
                    decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                target.text_valid = False
                return None, "decode_error"
            except OSError:
                return None, "member_read_error"
            target.text_valid = True
        return path, None

    def new_cursor(self, state: tuple[Any, ...]) -> str:
        self._cursor_number += 1
        token = f"cursor:{self._cursor_number}"
        self._cursors[token] = state
        return token

    def cursor_state(self, token: str, expected: tuple[Any, ...]) -> tuple[Any, ...]:
        state = self._cursors.get(token)
        if state is None or state[: len(expected)] != expected:
            raise InventoryError("invalid_cursor", "The browser cursor is invalid.")
        return state

    def handle_browser_request(self, request: dict[str, object]) -> dict[str, object]:
        """Execute one validated browser operation against cumulative state."""

        operation = str(request["operation"])
        if operation == "list_members":
            return self._list_members(request)
        if operation == "search_members":
            return self._search_members(request)
        if operation == "read_member":
            return self._read_member(request)
        raise InventoryError("invalid_request", "The browser request is invalid.")

    def _common_response(
        self,
        operation: str,
        status: str,
        issues: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        reached = [name for name in FIXED_LIMITS if name in set(self.reached_limits)]
        return {
            "status": status,
            "reached_limits": reached,
            "issues": issues or [],
            "browser_version": "pilot-v1",
            "artifact_sha256": self.snapshot.sha256,
            "operation": operation,
        }

    @staticmethod
    def _browser_issue(
        code: str, target_id: str | None, message: str
    ) -> dict[str, object]:
        return {"code": code, "target_id": target_id, "message": message}

    def _list_members(self, request: dict[str, object]) -> dict[str, object]:
        cursor = request["cursor"]
        if cursor is None:
            start = 0
        else:
            state = self.cursor_state(str(cursor), ("list_members",))
            start = int(state[1])
        limit = int(request["limit"])
        stop = min(start + limit, len(self.targets))
        next_cursor = (
            self.new_cursor(("list_members", stop))
            if stop < len(self.targets)
            else None
        )
        issues = []
        status = "complete"
        if self.inventory_status != "complete":
            status = "partial"
            issues.append(
                self._browser_issue(
                    "inventory_incomplete",
                    None,
                    "The checker inventory is incomplete.",
                )
            )
        response = self._common_response("list_members", status, issues)
        response.update(
            {
                "targets": [
                    self.target_listing(target) for target in self.targets[start:stop]
                ],
                "next_cursor": next_cursor,
                "inventory_complete": (
                    next_cursor is None and self.inventory_status == "complete"
                ),
            }
        )
        return response

    def _search_members(self, request: dict[str, object]) -> dict[str, object]:
        target_id = str(request["target_id"])
        query = str(request["query"])
        cursor = request["cursor"]
        limit = int(request["limit"])
        selected = (
            self.targets
            if target_id == "all"
            else [self.target_by_id[target_id]]
            if target_id in self.target_by_id
            else []
        )
        if not selected:
            response = self._common_response(
                "search_members",
                "error",
                [
                    self._browser_issue(
                        "target_not_found",
                        target_id,
                        "The requested browser target does not exist.",
                    )
                ],
            )
            response.update(
                {
                    "matches": [],
                    "next_cursor": None,
                    "search_complete": False,
                    "searched_target_count": 0,
                    "skipped_targets": [],
                    "searched_bytes": 0,
                }
            )
            return response
        if cursor is None:
            target_index, byte_offset, line = 0, 0, 1
        else:
            state = self.cursor_state(str(cursor), ("search_members", target_id, query))
            target_index, byte_offset, line = (
                int(state[3]),
                int(state[4]),
                int(state[5]),
            )
        matches: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        searched_targets = 0
        searched_bytes = 0
        next_cursor = None
        while target_index < len(selected):
            target = selected[target_index]
            path, reason = self.ensure_text(target)
            if path is None:
                skipped.append({"target_id": target.target_id, "reason_code": reason})
                target_index += 1
                byte_offset, line = 0, 1
                if len(skipped) >= 500:
                    next_cursor = self.new_cursor(
                        (
                            "search_members",
                            target_id,
                            query,
                            target_index,
                            byte_offset,
                            line,
                        )
                    )
                    break
                continue
            searched_targets += 1
            found, exhausted, next_byte, next_line, bytes_read = self._search_path(
                path,
                target.target_id,
                query,
                byte_offset,
                line,
                limit - len(matches),
            )
            matches.extend(found)
            searched_bytes += bytes_read
            if not exhausted:
                next_cursor = self.new_cursor(
                    (
                        "search_members",
                        target_id,
                        query,
                        target_index,
                        next_byte,
                        next_line,
                    )
                )
                break
            target_index += 1
            byte_offset, line = 0, 1
        search_complete = next_cursor is None and target_index >= len(selected)
        status = (
            "partial" if skipped or self.inventory_status != "complete" else "complete"
        )
        issues = []
        if self.inventory_status != "complete":
            issues.append(
                self._browser_issue(
                    "inventory_incomplete",
                    None,
                    "The checker inventory is incomplete.",
                )
            )
        response = self._common_response("search_members", status, issues)
        response.update(
            {
                "matches": matches,
                "next_cursor": next_cursor,
                "search_complete": search_complete,
                "searched_target_count": searched_targets,
                "skipped_targets": skipped,
                "searched_bytes": searched_bytes,
            }
        )
        return response

    def _search_path(
        self,
        path: Path,
        target_id: str,
        query: str,
        start_byte: int,
        start_line: int,
        limit: int,
    ) -> tuple[list[dict[str, object]], bool, int, int, int]:
        matches: list[dict[str, object]] = []
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        overlap = max(len(query) - 1, 240)
        buffer = ""
        buffer_byte = start_byte
        buffer_line = start_line
        bytes_read = 0
        with path.open("rb") as handle:
            handle.seek(start_byte)
            while True:
                chunk = handle.read(64 * 1024)
                bytes_read += len(chunk)
                end = not chunk
                decoded = decoder.decode(chunk, final=end)
                buffer += decoded
                safe_end = len(buffer) if end else max(0, len(buffer) - overlap)
                search_at = 0
                while search_at < safe_end:
                    index = buffer.find(query, search_at)
                    if index < 0 or index >= safe_end:
                        break
                    prefix = buffer[:index]
                    match_byte = buffer_byte + len(prefix.encode("utf-8"))
                    match_line = buffer_line + prefix.count("\n")
                    line_start = buffer.rfind("\n", 0, index) + 1
                    line_end = buffer.find("\n", index + len(query))
                    snippet_incomplete = False
                    if line_end < 0:
                        line_end = min(len(buffer), index + len(query) + 240)
                        snippet_incomplete = not end
                    snippet, truncated = _bounded_display(buffer[line_start:line_end])
                    matches.append(
                        {
                            "target_id": target_id,
                            "byte_offset": match_byte,
                            "line": match_line,
                            "snippet_display": snippet,
                            "snippet_truncated": (
                                truncated
                                or snippet_incomplete
                                or (line_start == 0 and buffer_byte > 0)
                            ),
                        }
                    )
                    first = query[0]
                    next_byte = match_byte + len(first.encode("utf-8"))
                    next_line = match_line + (1 if first == "\n" else 0)
                    if len(matches) >= limit:
                        return matches, False, next_byte, next_line, bytes_read
                    search_at = index + 1
                if end:
                    return matches, True, handle.tell(), buffer_line, bytes_read
                dropped = buffer[:safe_end]
                buffer = buffer[safe_end:]
                buffer_byte += len(dropped.encode("utf-8"))
                buffer_line += dropped.count("\n")

    def _read_member(self, request: dict[str, object]) -> dict[str, object]:
        target_id = str(request["target_id"])
        target = self.target_by_id.get(target_id)
        start_line = int(request["start_line"])
        max_lines = int(request["max_lines"])
        if target is None:
            return self._empty_read_response(
                target_id,
                start_line,
                "target_not_found",
                "The requested browser target does not exist.",
            )
        path, reason = self.ensure_text(target)
        if path is None:
            code = (
                "decode_error"
                if reason == "decode_error"
                else "member_read_error"
                if reason in {"member_read_error", "integrity_error"}
                else "target_not_readable"
            )
            return self._empty_read_response(
                target_id,
                start_line,
                code,
                "The requested browser target is not readable as supported text.",
            )
        (
            text,
            end_line,
            next_line,
            end_of_text,
            text_truncated,
            returned_bytes,
        ) = self._read_path_lines(path, start_line, max_lines)
        response = self._common_response("read_member", "complete")
        response.update(
            {
                "start_line": start_line,
                "end_line": end_line,
                "text": text,
                "next_line": next_line,
                "end_of_text": end_of_text,
                "text_truncated": text_truncated,
                "returned_bytes": returned_bytes,
            }
        )
        return response

    def _empty_read_response(
        self,
        target_id: str,
        start_line: int,
        code: str,
        message: str,
    ) -> dict[str, object]:
        response = self._common_response(
            "read_member",
            "error",
            [self._browser_issue(code, target_id, message)],
        )
        response.update(
            {
                "start_line": start_line,
                "end_line": None,
                "text": "",
                "next_line": None,
                "end_of_text": False,
                "text_truncated": False,
                "returned_bytes": 0,
            }
        )
        return response

    def _read_path_lines(
        self, path: Path, start_line: int, max_lines: int
    ) -> tuple[str, int | None, int | None, bool, bool, int]:
        byte_limit = 64 * 1024
        line_number = 1
        output: list[str] = []
        returned = 0
        truncated = False
        eof = False
        with path.open("rb") as handle:
            while line_number < start_line:
                chunk = handle.readline(64 * 1024)
                if not chunk:
                    eof = True
                    break
                if chunk.endswith(b"\n"):
                    line_number += 1
            if eof:
                return "", None, None, True, False, 0
            while len(output) < max_lines and returned < byte_limit:
                remaining = byte_limit - returned
                raw = handle.readline(remaining + 1)
                if not raw:
                    eof = True
                    break
                overlong = len(raw) > remaining
                kept = raw[:remaining] if overlong else raw
                decoded = ""
                while kept:
                    try:
                        decoded = kept.decode("utf-8")
                    except UnicodeDecodeError as error:
                        if error.reason != "unexpected end of data":
                            raise
                        kept = kept[:-1]
                    else:
                        break
                content = decoded.rstrip("\n").removesuffix("\r")
                output.append(f"{line_number}: {_display_text(content)}")
                returned += len(kept)
                if overlong or (not raw.endswith(b"\n") and returned >= byte_limit):
                    truncated = True
                    while raw and not raw.endswith(b"\n"):
                        raw = handle.readline(64 * 1024)
                    line_number += 1
                    break
                line_number += 1
            if not truncated:
                position = handle.tell()
                eof = not handle.read(1)
                handle.seek(position)
        end_line = start_line + len(output) - 1 if output else None
        next_line = None if eof else line_number
        return "\n".join(output), end_line, next_line, eof, truncated, returned

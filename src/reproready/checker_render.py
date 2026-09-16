"""Compact, evidence-first Rich rendering for static checker reports."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_Record = dict[str, object]
_COMPACT_SUBJECTS = 3
_BIDI_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}


@dataclass(frozen=True, slots=True)
class _Palette:
    identity_border: str
    content_band: str
    content_border: str
    incomplete_band: str
    incomplete_border: str
    finding_band: str
    finding_border: str
    review_band: str
    review_border: str
    metadata: str


_PALETTE = _Palette(
    identity_border="#4f7890",
    content_band="bold bright_white on #355c7d",
    content_border="#5d8098",
    incomplete_band="bold #2b2111 on #d8a84e",
    incomplete_border="#aa7a27",
    finding_band="bold bright_white on #6e4b70",
    finding_border="#806282",
    review_band="bold #102926 on #66b3a6",
    review_border="#4f8f86",
    metadata="#74828c",
)

_KIND_TITLES = {
    "direct_python": "Python file",
    "direct_notebook": "Notebook",
    "direct_requirements": "Requirements file",
    "direct_pyproject": "pyproject.toml",
    "zip": "ZIP archive",
    "zip64": "ZIP64 archive",
    "unsupported_tar": "Unsupported TAR archive",
    "unsupported_gzip": "Unsupported gzip file",
    "unsupported_pdf": "Unsupported PDF",
    "unsupported_docx": "Unsupported Word document",
    "unsupported_regular": "Unsupported regular file",
    "unclassified": "Unclassified input",
}
_RULE_TITLES = {
    "archive.structure": "Archive structure",
    "python.absolute-path": "Absolute paths",
    "python.dependencies": "Dependencies",
    "python.sys-path-three-dot": "Python search paths",
    "python.download-comment-http-url": "Download comments",
    "python.open-bundled-archive-member": "Files inside archives",
    "python.pandas-csv-inventory-absence": "CSV files",
    "python.notebook-pip-install": "Notebook setup",
    "python.gdown-anonymized-value": "Download identifiers",
    "python.entry-point-input": "User input",
    "python.gfile-bucket-authority": "Cloud storage",
}
_CONDITION_TITLES = {
    "absolute_member_path": "Absolute archive member paths",
    "parent_path_segment": "Parent segments in archive member paths",
    "control_character_in_name": "Control characters in archive member names",
    "duplicate_member_name": "Duplicate archive member names",
    "symlink_entry": "Symbolic-link archive entries",
    "special_entry": "Special archive entries",
    "encrypted_entry": "Encrypted archive entries",
    "unsupported_compression": "Unsupported archive compression",
    "integrity_error": "Archive integrity errors",
    "central_directory_error": "Archive central-directory error",
    "windows_device_path": "Windows device paths",
    "windows_unc_path": "Windows UNC paths",
    "windows_drive_path": "Windows drive paths",
    "posix_absolute_path": "POSIX absolute paths",
    "tilde_path": "Home-relative paths",
    "import_without_exact_declaration": "Imports without matching declarations",
    "declaration_without_exact_import": "Declarations without matching imports",
    "three_dot_path_segment": "Three-dot path segments",
    "download_comment_with_http_url": "Comments with download URLs",
    "read_path_only_in_bundled_archive": "Reads linked to archived files",
    "pandas_csv_not_in_inventory": "CSV paths absent from the artifact",
    "notebook_pip_install": "Notebook pip commands",
    "anonymized_download_identifier": "Anonymized download values",
    "entry_point_stdin": "Entry-point input calls",
    "bucket_authority_literal": "Literal bucket names",
}
_FINDING_EXPLANATIONS = {
    "archive.structure": (
        "Observed archive metadata or a bounded archive read. Filesystem behavior "
        "is not inferred."
    ),
    "python.absolute-path": (
        "Observed lexical paths in supported calls. Host files and call reachability "
        "were not inspected."
    ),
}


@dataclass(frozen=True, slots=True)
class _ReviewSpec:
    rule_id: str
    title: str
    action: str


_REVIEW_SPECS = (
    _ReviewSpec(
        "python.dependencies",
        "Dependencies",
        "Compare imports with dependency declarations.",
    ),
    _ReviewSpec(
        "python.sys-path-three-dot",
        "Python search paths",
        "Confirm whether each three-dot path is intentional and portable.",
    ),
    _ReviewSpec(
        "python.download-comment-http-url",
        "Download comments",
        "Decide whether each URL describes a required acquisition step.",
    ),
    _ReviewSpec(
        "python.open-bundled-archive-member",
        "Files inside archives",
        "Confirm how each archived file becomes available to its read call.",
    ),
    _ReviewSpec(
        "python.pandas-csv-inventory-absence",
        "CSV files",
        "Confirm how each absent CSV path is supplied.",
    ),
    _ReviewSpec(
        "python.notebook-pip-install",
        "Notebook setup",
        "Decide whether each install command is required setup.",
    ),
    _ReviewSpec(
        "python.gdown-anonymized-value",
        "Download identifiers",
        "Replace or explain each anonymized download value.",
    ),
    _ReviewSpec(
        "python.entry-point-input",
        "User input",
        "Confirm how standard input is supplied.",
    ),
    _ReviewSpec(
        "python.gfile-bucket-authority",
        "Cloud storage",
        "Confirm each literal bucket name and its access requirements.",
    ),
)

_LIMIT_TITLES = {
    "max_input_bytes": "Input size",
    "max_member_count": "Archive members",
    "max_nested_zip_depth": "Nested ZIP depth",
    "max_expanded_bytes_per_member": "Expanded member size",
    "max_expanded_bytes_total": "Total expanded size",
    "max_python_source_bytes": "Python source size",
    "max_notebook_bytes": "Notebook size",
    "max_dependency_file_bytes": "Dependency file size",
    "max_worker_rss_bytes": "Worker memory",
    "max_temporary_bytes": "Temporary storage",
    "max_elapsed_seconds": "Inspection time",
    "max_observations": "Reported observations",
    "max_report_bytes": "Encoded report size",
}
_LIMIT_REASONS = {
    "max_input_bytes": {"max_input_bytes"},
    "max_member_count": {"member_count_limit"},
    "max_nested_zip_depth": {"nested_zip_depth_limit"},
    "max_expanded_bytes_per_member": {"expanded_member_limit"},
    "max_expanded_bytes_total": {"expanded_total_limit"},
    "max_python_source_bytes": {"max_python_source_bytes", "python_source_limit"},
    "max_notebook_bytes": {"max_notebook_bytes", "notebook_limit"},
    "max_dependency_file_bytes": {
        "max_dependency_file_bytes",
        "dependency_file_limit",
    },
    "max_worker_rss_bytes": {"worker_memory_limit"},
    "max_temporary_bytes": {"temporary_storage_limit"},
    "max_elapsed_seconds": {"worker_timeout"},
    "max_observations": {"observation_limit"},
    "max_report_bytes": {"report_size_limit"},
}
_LIMIT_REASON_NAMES = {
    reason for reasons in _LIMIT_REASONS.values() for reason in reasons
}
_BYTE_LIMITS = {
    "max_input_bytes",
    "max_expanded_bytes_per_member",
    "max_expanded_bytes_total",
    "max_python_source_bytes",
    "max_notebook_bytes",
    "max_dependency_file_bytes",
    "max_worker_rss_bytes",
    "max_temporary_bytes",
    "max_report_bytes",
}
_REASON_TITLES = {
    "unsupported_format": "Unsupported input format",
    "unsupported_dependency_form": "Unsupported dependency file",
    "decode_error": "Text decoding failed",
    "member_read_error": "Archive member read failed",
    "dependency_parse_error": "Dependency parsing failed",
    "syntax_error": "Python parsing failed",
    "python_tokenize_error": "Python comment scanning failed",
    "notebook_parse_error": "Notebook parsing failed",
    "unsupported_notebook_version": "Unsupported notebook version",
    "unsupported_notebook_language": "Unsupported notebook language",
    "notebook_cell_source_error": "Notebook cell source is invalid",
    "unsupported_notebook_syntax": "Unsupported notebook syntax",
    "notebook_shell_parse_error": "Notebook command parsing failed",
    "checker_cache_error": "Checker workspace failed",
    "integrity_error": "Archive integrity check failed",
    "central_directory_error": "Archive directory could not be read",
    "source_changed": "Input changed during snapshotting",
    "worker_error": "Inspection worker failed",
}
_REASON_EXPLANATIONS = {
    "unsupported_format": "This top-level file format is not supported by checker v1.",
    "unsupported_dependency_form": (
        "Declarations in this dependency-file format were not parsed."
    ),
    "decode_error": "The retained bytes could not be decoded as supported text.",
    "syntax_error": (
        "AST-based checks could not inspect this source. Independent token-based "
        "results remain available when produced."
    ),
    "unsupported_notebook_syntax": (
        "AST-based checks skipped this cell; notebook-command checks may still apply."
    ),
    "source_changed": "No inspection result was accepted for a changing input.",
    "worker_error": "The worker stopped before it produced a usable result.",
}


@dataclass(slots=True)
class _ReportIndex:
    report: _Record
    members: dict[str, _Record]
    sources: dict[str, _Record]
    results: list[_Record]
    observations_by_rule: dict[str, list[_Record]]
    evidence_by_id: dict[str, _Record]


@dataclass(slots=True)
class _ObservationGroup:
    rule_id: str
    condition_code: str
    observations: list[_Record]


@dataclass(slots=True)
class _SubjectGroup:
    label: object
    observations: list[_Record]


@dataclass(slots=True)
class _CoverageGroup:
    field_name: str
    reason: str
    records: list[_Record]
    scopes: list[str]
    message: object | None


def report_console(*, file: TextIO | None = None) -> Console:
    """Construct a report console with automatic terminal behavior."""

    no_color = os.environ.get("NO_COLOR") is not None
    return Console(
        file=file,
        no_color=no_color,
        color_system=None if no_color else "auto",
    )


def _terminal_text(value: object, style: str | None = None) -> Text:
    escaped: list[str] = []
    for character in str(value):
        codepoint = ord(character)
        if (
            codepoint < 32
            or codepoint == 127
            or codepoint in _BIDI_CODEPOINTS
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(character)
    return Text("".join(escaped), style=style)


def _append(line: Text, value: object, style: str | None = None) -> None:
    line.append_text(_terminal_text(value, style))


def _count(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _humanize_code(value: str) -> str:
    replacements = {
        "csv": "CSV",
        "gfile": "GFile",
        "http": "HTTP",
        "python": "Python",
        "zip": "ZIP",
    }
    words = value.replace("_", " ").strip().split()
    normalized = [replacements.get(word.casefold(), word) for word in words]
    if normalized and normalized[0] not in replacements.values():
        normalized[0] = normalized[0].capitalize()
    return " ".join(normalized)


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _limit_value(limit: str, value: object) -> str:
    if isinstance(value, int) and limit in _BYTE_LIMITS:
        return _human_size(value)
    if limit == "max_elapsed_seconds":
        return f"{value} seconds"
    return str(value)


def _print_band(console: Console, title: str, style: str) -> None:
    console.print()
    band = Table.grid(expand=True, padding=(0, 1))
    band.add_row(Text(title, style="bold"), style=style)
    console.print(band)


def _index_report(report: _Record) -> _ReportIndex:
    inventory = cast(_Record, report["inventory"])
    members = {
        cast(str, member["member_id"]): member
        for member in cast(list[_Record], inventory["members"])
    }
    sources = {
        cast(str, source["source_id"]): source
        for source in cast(list[_Record], report["source_index"])
    }
    results = cast(list[_Record], report["rule_results"])
    observations_by_rule: dict[str, list[_Record]] = {}
    evidence_by_id: dict[str, _Record] = {}
    for result in results:
        rule_id = cast(str, result["rule_id"])
        observations_by_rule[rule_id] = cast(list[_Record], result["observations"])
        for evidence in cast(list[_Record], result["evidence"]):
            evidence_by_id[cast(str, evidence["evidence_id"])] = evidence
    return _ReportIndex(
        report,
        members,
        sources,
        results,
        observations_by_rule,
        evidence_by_id,
    )


def _member_name(member: _Record) -> str:
    name = cast(str, member["name"])
    ordinal = cast(int, member["duplicate_ordinal"])
    return name if ordinal == 1 else f"{name} (duplicate {ordinal})"


def _member_location(member_id: str, members: dict[str, _Record]) -> str:
    names: list[str] = []
    seen: set[str] = set()
    current: str | None = member_id
    while current is not None and current in members and current not in seen:
        seen.add(current)
        member = members[current]
        names.append(_member_name(member))
        current = cast(str | None, member.get("parent_member_id"))
    names.reverse()
    return " → ".join(names)


def _resolved_coordinates(
    item: _Record, index: _ReportIndex
) -> tuple[str | None, str | None, object | None, object | None]:
    source_id = cast(str | None, item.get("source_id"))
    source = index.sources.get(source_id) if source_id is not None else None
    member_id = cast(str | None, item.get("member_id"))
    if member_id is None and source is not None:
        member_id = cast(str | None, source.get("member_id"))
    cell = item.get("cell")
    if cell is None and source is not None:
        cell = source.get("cell")
    return member_id, source_id, cell, item.get("line")


def _location(item: _Record, index: _ReportIndex) -> str:
    member_id, _source_id, cell, line = _resolved_coordinates(item, index)
    if member_id is not None and member_id in index.members:
        base = _member_location(member_id, index.members)
    else:
        artifact = cast(_Record, index.report["artifact"])
        base = cast(str, artifact["display_name"])
    parts = [base]
    if cell is not None:
        parts.append(f"cell {cell}")
    if line is not None:
        parts.append(f"line {line}")
    return " / ".join(parts)


def _location_key(item: _Record, index: _ReportIndex) -> tuple[object, ...]:
    return _resolved_coordinates(item, index)


def _unique_records(records: list[_Record], index: _ReportIndex) -> list[_Record]:
    unique: list[_Record] = []
    seen: set[tuple[object, ...]] = set()
    for record in records:
        key = _location_key(record, index)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _print_identity(console: Console, index: _ReportIndex) -> None:
    artifact = cast(_Record, index.report["artifact"])
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column(ratio=1, overflow="fold")
    details.add_row("Name", _terminal_text(artifact["display_name"]))
    details.add_row(
        "Format",
        _terminal_text(
            _KIND_TITLES.get(
                cast(str, artifact["detected_kind"]),
                cast(str, artifact["detected_kind"]),
            )
        ),
    )
    details.add_row(
        "Size", _terminal_text(_human_size(cast(int, artifact["size_bytes"])))
    )
    if not artifact["snapshot_complete"]:
        details.add_row("Snapshot", Text("Incomplete", style="bold"))
    console.print(
        Panel(
            details,
            title="Artifact",
            title_align="left",
            border_style=_PALETTE.identity_border,
            expand=True,
        )
    )


def _source_rows(index: _ReportIndex) -> list[tuple[str, int, int, int]]:
    labels = (
        ("Python files", {"python_file"}),
        ("Notebooks", {"notebook_document"}),
        ("Notebook code cells", {"notebook_code_cell"}),
        ("Dependency files", {"requirements", "pyproject", "unsupported_source"}),
    )
    sources = list(index.sources.values())
    rows: list[tuple[str, int, int, int]] = []
    for label, forms in labels:
        matching = [source for source in sources if source["form"] in forms]
        if not matching:
            continue
        analyzed = sum(source["status"] == "inspected" for source in matching)
        rows.append((label, len(matching), analyzed, len(matching) - analyzed))
    return rows


def _content_panel(index: _ReportIndex) -> Group:
    renderables: list[RenderableType] = []
    kinds = Counter(cast(str, member["kind"]) for member in index.members.values())
    inventory_rows = (
        ("Files", kinds["file"]),
        ("Folders", kinds["directory"]),
        ("Nested ZIPs", kinds["zip"]),
        ("Symbolic links", kinds["symlink"]),
        ("Special entries", kinds["special"] + kinds["unknown"]),
    )
    if index.members:
        inventory = cast(_Record, index.report["inventory"])
        inventory_table = Table(box=None, pad_edge=False, expand=True)
        inventory_table.add_column("Archive inventory", style="bold")
        inventory_table.add_column("Count", justify="right")
        for label, count in inventory_rows:
            if count:
                inventory_table.add_row(label, str(count))
        if inventory["status"] != "complete":
            inventory_table.add_row(
                "Inventory status", _terminal_text(inventory["status"])
            )
        renderables.append(inventory_table)

    source_rows = _source_rows(index)
    if source_rows:
        source_table = Table(box=None, pad_edge=False, expand=True)
        source_table.add_column("Source content", style="bold")
        source_table.add_column("Found", justify="right")
        source_table.add_column("Analyzed", justify="right")
        source_table.add_column("Incomplete", justify="right")
        for label, found, analyzed, incomplete in source_rows:
            source_table.add_row(
                label,
                str(found),
                str(analyzed),
                str(incomplete) if incomplete else "—",
            )
        if renderables:
            renderables.append(Text(""))
        renderables.append(source_table)

    if not renderables:
        renderables.append(Text("No supported source content was analyzed."))
    return Group(*renderables)


def _coverage_groups(index: _ReportIndex) -> list[_CoverageGroup]:
    groups: dict[tuple[str, str], _CoverageGroup] = {}
    seen_records: dict[tuple[str, str], set[tuple[object, ...]]] = {}
    for result in index.results:
        rule_id = cast(str, result["rule_id"])
        for field_name in ("skipped_inputs", "failed_inputs"):
            for record in cast(list[_Record], result[field_name]):
                reason = cast(str, record["reason_code"])
                key = (field_name, reason)
                group = groups.get(key)
                if group is None:
                    group = _CoverageGroup(
                        field_name,
                        reason,
                        [],
                        [],
                        record.get("message"),
                    )
                    groups[key] = group
                    seen_records[key] = set()
                if rule_id not in group.scopes:
                    group.scopes.append(rule_id)
                location = _location_key(record, index)
                if location not in seen_records[key]:
                    seen_records[key].add(location)
                    group.records.append(record)

    for source in index.sources.values():
        if source["status"] == "inspected":
            continue
        field_name = (
            "failed_inputs" if source["status"] == "error" else "skipped_inputs"
        )
        reason = cast(str, source["reason_code"] or "unspecified")
        key = (field_name, reason)
        group = groups.get(key)
        if group is None:
            group = _CoverageGroup(field_name, reason, [], [], None)
            groups[key] = group
            seen_records[key] = set()
        location = _location_key(source, index)
        if location not in seen_records[key]:
            seen_records[key].add(location)
            group.records.append(source)
    return list(groups.values())


def _dependency_format(name: str) -> str:
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    folded = basename.casefold()
    if folded.startswith(("environment", "conda")) and folded.endswith(
        (".yml", ".yaml")
    ):
        return "Conda environment file"
    if folded in {"pipfile", "pipfile.lock"}:
        return "Pipenv file"
    if folded == "setup.py":
        return "setuptools file"
    if folded.startswith("dockerfile"):
        return "Dockerfile"
    if folded.startswith(("requirements", "constraints")):
        return "pip input file"
    if folded.endswith(".lock"):
        return "lock file"
    return "dependency file"


def _record_format(record: _Record, index: _ReportIndex) -> str:
    source_id = cast(str | None, record.get("source_id"))
    source = index.sources.get(source_id) if source_id is not None else None
    member_id = cast(str | None, record.get("member_id"))
    if member_id is None and source is not None:
        member_id = cast(str | None, source.get("member_id"))
    member = index.members.get(member_id) if member_id is not None else None
    if source is not None:
        form = cast(str, source["form"])
        if form == "unsupported_source" and member is not None:
            return _dependency_format(cast(str, member["name"]))
        return {
            "python_file": "Python file",
            "notebook_document": "notebook",
            "notebook_code_cell": "notebook code cell",
            "requirements": "requirements file",
            "pyproject": "pyproject.toml",
            "unsupported_source": "dependency file",
        }.get(form, "source")
    if member is not None:
        return {
            "file": "archive file",
            "directory": "archive folder",
            "zip": "nested ZIP",
            "symlink": "symbolic link",
            "special": "special archive entry",
            "unknown": "archive entry",
        }.get(cast(str, member["kind"]), "archive entry")
    artifact = cast(_Record, index.report["artifact"])
    return _KIND_TITLES.get(cast(str, artifact["detected_kind"]), "artifact").casefold()


def _joined_checks(rule_ids: list[str]) -> Text:
    line = Text()
    for position, rule_id in enumerate(rule_ids):
        if position:
            line.append(", ")
        line.append(_RULE_TITLES.get(rule_id, rule_id))
    return line


def _detail_line(
    shown: int,
    total: int,
    unit: str,
    report_path: Path | None,
) -> Text | None:
    if shown >= total:
        return None
    omitted = total - shown
    line = Text(
        f"Showing {shown} of {total} {_count(total, unit).split(' ', 1)[1]}; "
        f"{omitted} omitted from this display. ",
        style="dim",
    )
    if report_path is not None:
        line.append("See all retained detail in ")
        _append(line, report_path)
        line.append(" with --all.")
    else:
        line.append("Use check INPUT --json to save the complete retained report.")
    return line


def _coverage_panel(
    group: _CoverageGroup,
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
) -> Panel:
    title = _REASON_TITLES.get(group.reason, "Inspection incomplete")
    if group.reason == "unsupported_dependency_form" and group.records:
        formats = {_record_format(record, index) for record in group.records}
        if len(formats) == 1:
            title = next(iter(formats)).capitalize()

    records = _unique_records(group.records, index)
    shown = records if show_all else records[:_COMPACT_SUBJECTS]
    content = Table(box=None, pad_edge=False, expand=True)
    content.add_column("Format", style="bold")
    content.add_column("Affected content", overflow="fold")
    for record in shown:
        content.add_row(
            _record_format(record, index),
            _terminal_text(_location(record, index)),
        )
    if not records:
        artifact = cast(_Record, index.report["artifact"])
        content.add_row(
            _KIND_TITLES.get(cast(str, artifact["detected_kind"]), "Artifact"),
            _terminal_text(artifact["display_name"]),
        )

    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="bold")
    rows.add_column(ratio=1, overflow="fold")
    if group.scopes:
        rows.add_row("Affected checks", _joined_checks(group.scopes))
    explanation = _REASON_EXPLANATIONS.get(group.reason, group.message)
    if explanation is not None:
        rows.add_row("Result", _terminal_text(explanation))

    renderables: list[RenderableType] = [rows, Text(""), content]
    detail = _detail_line(len(shown), len(records), "affected item", report_path)
    if detail is not None:
        renderables.extend((Text(""), detail))
    return Panel(
        Group(*renderables),
        title=title,
        title_align="left",
        border_style=_PALETTE.incomplete_border,
        expand=True,
    )


def _print_incomplete_sections(
    console: Console,
    groups: list[_CoverageGroup],
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
) -> None:
    ordinary = [group for group in groups if group.reason not in _LIMIT_REASON_NAMES]
    for field_name, title in (
        ("skipped_inputs", "Checks skipped"),
        ("failed_inputs", "Checks failed"),
    ):
        matching = [group for group in ordinary if group.field_name == field_name]
        if not matching:
            continue
        _print_band(console, title, _PALETTE.incomplete_band)
        for group in matching:
            console.print(
                _coverage_panel(
                    group,
                    index,
                    report_path=report_path,
                    show_all=show_all,
                )
            )


def _print_limits(
    console: Console,
    groups: list[_CoverageGroup],
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
) -> None:
    limits = cast(_Record, index.report["limits"])
    reached = cast(list[str], limits["reached"])
    if not reached:
        return
    effective = cast(_Record, limits["effective"])
    _print_band(console, "Limits reached", _PALETTE.incomplete_band)
    for limit in reached:
        reasons = _LIMIT_REASONS.get(limit, set())
        related = [group for group in groups if group.reason in reasons]
        records = _unique_records(
            [record for group in related for record in group.records], index
        )
        scopes: list[str] = []
        for group in related:
            for scope in group.scopes:
                if scope not in scopes:
                    scopes.append(scope)
        shown = records if show_all else records[:_COMPACT_SUBJECTS]

        rows = Table.grid(padding=(0, 2))
        rows.add_column(style="bold")
        rows.add_column(ratio=1, overflow="fold")
        rows.add_row("Limit", _limit_value(limit, effective[limit]))
        if scopes:
            rows.add_row("Affected checks", _joined_checks(scopes))
        if shown:
            locations = Text()
            for position, record in enumerate(shown):
                if position:
                    locations.append("\n")
                locations.append_text(_terminal_text(_location(record, index)))
            rows.add_row("Affected content", locations)
        else:
            artifact = cast(_Record, index.report["artifact"])
            rows.add_row("Affected content", _terminal_text(artifact["display_name"]))

        renderables: list[RenderableType] = [rows]
        detail = _detail_line(len(shown), len(records), "affected item", report_path)
        if detail is not None:
            renderables.extend((Text(""), detail))
        console.print(
            Panel(
                Group(*renderables),
                title=_LIMIT_TITLES.get(limit, limit),
                title_align="left",
                border_style=_PALETTE.incomplete_border,
                expand=True,
            )
        )


def _observation_groups(
    observations: list[_Record], rule_id: str
) -> list[_ObservationGroup]:
    groups: dict[str, _ObservationGroup] = {}
    for observation in observations:
        if observation["condition_code"] == "resource_limit_reached":
            continue
        condition = cast(str, observation["condition_code"])
        group = groups.get(condition)
        if group is None:
            group = _ObservationGroup(rule_id, condition, [])
            groups[condition] = group
        group.observations.append(observation)
    return list(groups.values())


def _dependency_subject(observation: _Record, index: _ReportIndex) -> object:
    wanted = (
        "python_import"
        if observation["condition_code"] == "import_without_exact_declaration"
        else "dependency_declaration"
    )
    for evidence_id in cast(list[str], observation["evidence_ids"]):
        evidence = index.evidence_by_id.get(evidence_id)
        if evidence is not None and evidence["kind"] == wanted:
            return evidence["value"]
    return observation.get("snippet") or _location(observation, index)


def _subject_groups(
    group: _ObservationGroup, index: _ReportIndex
) -> list[_SubjectGroup]:
    subjects: list[_SubjectGroup] = []
    by_label: dict[str, _SubjectGroup] = {}
    dependency = group.rule_id == "python.dependencies"
    for observation in group.observations:
        label = (
            _dependency_subject(observation, index)
            if dependency
            else observation.get("snippet") or _location(observation, index)
        )
        key = str(label) if dependency else cast(str, observation["observation_id"])
        subject = by_label.get(key)
        if subject is None:
            subject = _SubjectGroup(label, [])
            by_label[key] = subject
            subjects.append(subject)
        subject.observations.append(observation)
    return subjects


def _linked_evidence(observation: _Record, index: _ReportIndex) -> list[_Record]:
    selected: list[_Record] = []
    seen: set[str] = set()

    def add(evidence_id: str) -> None:
        if evidence_id in seen:
            return
        evidence = index.evidence_by_id.get(evidence_id)
        if evidence is None:
            return
        seen.add(evidence_id)
        selected.append(evidence)
        for related in cast(list[str], evidence["related_evidence_ids"]):
            add(related)

    for evidence_id in cast(list[str], observation["evidence_ids"]):
        add(evidence_id)
    return selected


def _useful_evidence(
    evidence: _Record,
    observation: _Record,
    group: _ObservationGroup,
    index: _ReportIndex,
) -> bool:
    if group.rule_id == "python.dependencies" and evidence["kind"] in {
        "python_import",
        "dependency_declaration",
    }:
        return False
    same_location = _location_key(evidence, index) == _location_key(observation, index)
    return not (
        same_location
        and evidence.get("value")
        in {
            observation.get("snippet"),
            _dependency_subject(observation, index),
        }
    )


def _summary_line(
    group: _ObservationGroup, subjects: list[_SubjectGroup], index: _ReportIndex
) -> Text:
    locations = {
        _location_key(observation, index) for observation in group.observations
    }
    line = Text(
        f"{_count(len(group.observations), 'occurrence')} across "
        f"{_count(len(locations), 'location')}"
    )
    if group.rule_id == "python.dependencies":
        line.append(f" and {_count(len(subjects), 'name')}")
    return line


def _observation_table(
    group: _ObservationGroup,
    subjects: list[_SubjectGroup],
    index: _ReportIndex,
    *,
    show_all: bool,
) -> tuple[Table, int, int]:
    selected_subjects = subjects if show_all else subjects[:_COMPACT_SUBJECTS]
    table = Table(box=None, pad_edge=False, expand=True)
    table.add_column("Evidence", style="bold", ratio=2, overflow="fold")
    table.add_column("Occurrences", justify="right", no_wrap=True)
    table.add_column("Location", ratio=3, overflow="fold")
    displayed_locations: set[tuple[object, ...]] = set()

    for subject in selected_subjects:
        observations = subject.observations if show_all else subject.observations[:1]
        for position, observation in enumerate(observations):
            label = subject.label if position == 0 else ""
            table.add_row(
                _terminal_text(label),
                str(len(subject.observations)) if position == 0 else "",
                _terminal_text(_location(observation, index)),
            )
            displayed_locations.add(_location_key(observation, index))
            snippet = observation.get("snippet")
            if (
                group.rule_id == "python.dependencies"
                and snippet is not None
                and str(snippet) != str(subject.label)
            ):
                context = Text("Context: ", style="dim")
                _append(context, snippet, "dim")
                table.add_row(context, "", "")
            evidence_records = [
                evidence
                for evidence in _linked_evidence(observation, index)
                if _useful_evidence(evidence, observation, group, index)
            ]
            if not show_all:
                evidence_records = evidence_records[:1]
            for evidence in evidence_records:
                relation = Text("Related: ", style="dim")
                relation.append(str(evidence["kind"]).replace("_", " "), style="dim")
                relation.append(" = ", style="dim")
                _append(relation, evidence["value"], "dim")
                table.add_row(
                    relation,
                    "",
                    _terminal_text(_location(evidence, index), "dim"),
                )
    return table, len(selected_subjects), len(displayed_locations)


def _observation_detail_line(
    group: _ObservationGroup,
    subjects: list[_SubjectGroup],
    displayed_subjects: int,
    displayed_locations: int,
    index: _ReportIndex,
    report_path: Path | None,
) -> Text | None:
    locations = {
        _location_key(observation, index) for observation in group.observations
    }
    if displayed_subjects == len(subjects) and displayed_locations == len(locations):
        return None
    subject_unit = "name" if group.rule_id == "python.dependencies" else "item"
    line = Text(
        f"Showing {_count(displayed_subjects, subject_unit)} and "
        f"{_count(displayed_locations, 'location')} from "
        f"{_count(len(subjects), subject_unit)} and {_count(len(locations), 'location')}. ",
        style="dim",
    )
    if report_path is not None:
        line.append("See all retained detail in ")
        _append(line, report_path)
        line.append(" with --all.")
    else:
        line.append("Use check INPUT --json to save the complete retained report.")
    return line


def _observation_body(
    group: _ObservationGroup,
    index: _ReportIndex,
    *,
    action: str | None,
    report_path: Path | None,
    show_all: bool,
) -> Group:
    subjects = _subject_groups(group, index)
    table, displayed_subjects, displayed_locations = _observation_table(
        group,
        subjects,
        index,
        show_all=show_all,
    )
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column(ratio=1, overflow="fold")
    if action is not None:
        summary.add_row("Action", action)
    summary.add_row("Reported", _summary_line(group, subjects, index))

    renderables: list[RenderableType] = [summary, Text(""), table]
    detail = _observation_detail_line(
        group,
        subjects,
        displayed_subjects,
        displayed_locations,
        index,
        report_path,
    )
    if detail is not None:
        renderables.extend((Text(""), detail))
    shortened = sum(
        bool(observation["snippet_truncated"]) for observation in group.observations
    )
    if shortened:
        renderables.extend(
            (
                Text(""),
                Text(
                    f"{_count(shortened, 'retained snippet')} shortened by the "
                    "report limit.",
                    style="dim",
                ),
            )
        )
    return Group(*renderables)


def _print_findings(
    console: Console,
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
) -> int:
    groups: list[_ObservationGroup] = []
    for rule_id in ("archive.structure", "python.absolute-path"):
        observations = [
            observation
            for observation in index.observations_by_rule.get(rule_id, [])
            if observation["kind"] == "finding"
        ]
        groups.extend(_observation_groups(observations, rule_id))
    if not groups:
        _print_band(console, "Findings", _PALETTE.finding_band)
        console.print("No artifact findings reported.")
        return 0

    total = sum(len(group.observations) for group in groups)
    _print_band(console, "Findings", _PALETTE.finding_band)
    console.print(Text(_count(total, "artifact finding"), style="bold"))
    for group in groups:
        body = _observation_body(
            group,
            index,
            action=None,
            report_path=report_path,
            show_all=show_all,
        )
        explanation = _FINDING_EXPLANATIONS.get(group.rule_id)
        if explanation is not None:
            body = Group(Text(explanation), Text(""), body)
        console.print(
            Panel(
                body,
                title=_CONDITION_TITLES.get(
                    group.condition_code, _humanize_code(group.condition_code)
                ),
                title_align="left",
                border_style=_PALETTE.finding_border,
                expand=True,
            )
        )
    return total


def _print_review(
    console: Console,
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
) -> int:
    categories: list[tuple[_ReviewSpec, list[_ObservationGroup]]] = []
    for spec in _REVIEW_SPECS:
        observations = [
            observation
            for observation in index.observations_by_rule.get(spec.rule_id, [])
            if observation["kind"] == "needs_human_review"
        ]
        groups = _observation_groups(observations, spec.rule_id)
        if groups:
            categories.append((spec, groups))
    if not categories:
        _print_band(console, "Needs review", _PALETTE.review_band)
        console.print("No review items reported.")
        return 0

    total = sum(
        len(group.observations) for _spec, groups in categories for group in groups
    )
    _print_band(console, "Needs review", _PALETTE.review_band)
    console.print(
        Text(
            f"{_count(total, 'review item')} in "
            f"{_count(len(categories), 'category', 'categories')}",
            style="bold",
        )
    )
    for spec, groups in categories:
        action = Table.grid(padding=(0, 2))
        action.add_column(style="bold")
        action.add_column(ratio=1, overflow="fold")
        action.add_row("Action", spec.action)
        renderables: list[RenderableType] = [action]
        for group in groups:
            renderables.append(Text(""))
            if spec.rule_id == "python.dependencies" or len(groups) > 1:
                renderables.append(
                    Text(
                        _CONDITION_TITLES.get(
                            group.condition_code,
                            _humanize_code(group.condition_code),
                        ),
                        style="bold",
                    )
                )
            renderables.append(
                _observation_body(
                    group,
                    index,
                    action=None,
                    report_path=report_path,
                    show_all=show_all,
                )
            )
        console.print(
            Panel(
                Group(*renderables),
                title=spec.title,
                title_align="left",
                border_style=_PALETTE.review_border,
                expand=True,
            )
        )
    return total


def _print_report_reference(console: Console, report_path: Path) -> None:
    _print_band(console, "Saved report", _PALETTE.content_band)
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="bold")
    rows.add_column(ratio=1, overflow="fold")
    rows.add_row("Path", _terminal_text(report_path))
    rows.add_row("More detail", "Add --all to the view command.")
    console.print(rows)


def render_report(
    report: dict[str, object],
    console: Console,
    *,
    report_path: Path | None = None,
    show_all: bool = False,
) -> None:
    """Render one completed schema document without inspecting its source."""

    index = _index_report(cast(_Record, report))
    coverage = _coverage_groups(index)

    _print_identity(console, index)
    _print_band(console, "Content analyzed", _PALETTE.content_band)
    console.print(
        Panel(
            _content_panel(index),
            title="Artifact contents",
            title_align="left",
            border_style=_PALETTE.content_border,
            expand=True,
        )
    )
    _print_incomplete_sections(
        console,
        coverage,
        index,
        report_path=report_path,
        show_all=show_all,
    )
    _print_limits(
        console,
        coverage,
        index,
        report_path=report_path,
        show_all=show_all,
    )
    _print_findings(
        console,
        index,
        report_path=report_path,
        show_all=show_all,
    )
    _print_review(
        console,
        index,
        report_path=report_path,
        show_all=show_all,
    )
    if report_path is not None:
        _print_report_reference(console, report_path)

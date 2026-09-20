"""Compact, evidence-first Rich rendering for static checker reports."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO, cast

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


SectionKey = Literal[
    "artifact",
    "content",
    "limits",
    "skipped",
    "failed",
    "findings",
    "review",
    "saved",
]


@dataclass(frozen=True, slots=True)
class SectionSpec:
    key: SectionKey
    title: str
    short_title: str
    meaning: str


SECTION_SPECS = (
    SectionSpec(
        "artifact",
        "Artifact",
        "Artifact",
        "Identifies the input described by this saved report.",
    ),
    SectionSpec(
        "content",
        "Content analyzed",
        "Content",
        "Lists the input's contents separately from the source content the checker analyzed.",
    ),
    SectionSpec(
        "limits",
        "Limits reached",
        "Limits",
        "Shows recorded bounds on inspection or saved report detail.",
    ),
    SectionSpec(
        "skipped",
        "Checks skipped",
        "Skipped",
        "Shows content or checks omitted from inspection.",
    ),
    SectionSpec(
        "failed",
        "Checks failed",
        "Failed",
        "Shows errors encountered during inspection.",
    ),
    SectionSpec(
        "findings",
        "Findings",
        "Findings",
        "Shows saved observations from archive-structure and absolute-path checks.",
    ),
    SectionSpec(
        "review",
        "Needs review",
        "Review",
        "Shows saved observations that call for human interpretation.",
    ),
    SectionSpec(
        "saved",
        "Saved report",
        "Saved",
        "Identifies the saved report and the detail available without reopening the original input.",
    ),
)
SECTION_KEYS: tuple[SectionKey, ...] = tuple(spec.key for spec in SECTION_SPECS)
_SECTION_BY_KEY = {spec.key: spec for spec in SECTION_SPECS}


@dataclass(frozen=True, slots=True)
class _Palette:
    accent: str
    attention: str
    error: str
    location: str
    box_border: str


_PALETTE = _Palette(
    accent="#5ca89e",
    attention="#c99a45",
    error="#c66f6f",
    location="#6f9ec5",
    box_border="#77746b",
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
        "What explains each import or declaration without an exact match?",
    ),
    _ReviewSpec(
        "python.sys-path-three-dot",
        "Python search paths",
        "Is each three-dot path intentional and portable?",
    ),
    _ReviewSpec(
        "python.download-comment-http-url",
        "Download comments",
        "Does each URL describe a required acquisition step?",
    ),
    _ReviewSpec(
        "python.open-bundled-archive-member",
        "Files inside archives",
        "How does each archived file become available to its read call?",
    ),
    _ReviewSpec(
        "python.pandas-csv-inventory-absence",
        "CSV files",
        "How is each CSV file supplied?",
    ),
    _ReviewSpec(
        "python.notebook-pip-install",
        "Notebook setup",
        "Is each install command required setup?",
    ),
    _ReviewSpec(
        "python.gdown-anonymized-value",
        "Download identifiers",
        "What does each anonymized download value represent?",
    ),
    _ReviewSpec(
        "python.entry-point-input",
        "User input",
        "How is standard input supplied?",
    ),
    _ReviewSpec(
        "python.gfile-bucket-authority",
        "Cloud storage",
        "What does each literal bucket name identify, and what access does it require?",
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
_LIMIT_EXPLANATIONS = {
    "max_input_bytes": (
        "The input exceeded the byte limit for creating the inspection snapshot."
    ),
    "max_member_count": (
        "The archive-entry limit stopped further inventory inspection."
    ),
    "max_nested_zip_depth": (
        "A nested ZIP was inventoried but not opened because it exceeded the "
        "supported nesting depth."
    ),
    "max_expanded_bytes_per_member": (
        "An archive entry exceeded the per-entry expanded-byte limit."
    ),
    "max_expanded_bytes_total": (
        "The total expanded-byte limit restricted archive inspection."
    ),
    "max_python_source_bytes": (
        "A Python file or notebook code cell exceeded the source-size limit for "
        "analysis."
    ),
    "max_notebook_bytes": ("A notebook exceeded the notebook-size limit for analysis."),
    "max_dependency_file_bytes": (
        "A dependency file exceeded the dependency-file-size limit for analysis."
    ),
    "max_worker_rss_bytes": (
        "The inspection worker reached its resident-memory limit."
    ),
    "max_temporary_bytes": "The checker reached its temporary-storage bound.",
    "max_elapsed_seconds": ("The inspection worker reached its elapsed-time limit."),
    "max_observations": (
        "The observation bound restricted saved results and may also have stopped "
        "further observation collection."
    ),
    "max_report_bytes": (
        "The encoded-report bound restricted saved records and may also have "
        "stopped further inspection."
    ),
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


def _spaced_group(renderables: list[RenderableType]) -> Group:
    spaced: list[RenderableType] = []
    for renderable in renderables:
        if spaced:
            spaced.append(Text(""))
        spaced.append(renderable)
    return Group(*spaced)


def _section_heading(spec: SectionSpec) -> Text:
    return Text(spec.title, style=f"bold {_PALETTE.accent}")


def _summary_block(content: RenderableType) -> Table:
    summary = Table.grid(expand=True, padding=(0, 1))
    summary.add_column(style="bold", no_wrap=True)
    summary.add_column(ratio=1, overflow="fold")
    summary.add_row("Summary", content)
    return summary


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


def _identity_panel(index: _ReportIndex) -> Panel:
    artifact = cast(_Record, index.report["artifact"])
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold", no_wrap=True)
    details.add_column(ratio=1, overflow="fold")
    details.add_row("Name", _terminal_text(artifact["display_name"], _PALETTE.accent))
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
        "Size",
        _terminal_text(_human_size(cast(int, artifact["size_bytes"]))),
    )
    if not artifact["snapshot_complete"]:
        details.add_row("Snapshot", Text("Incomplete", style="bold"))
    return Panel(
        details,
        title=Text("Artifact identity", style="bold"),
        title_align="left",
        border_style=_PALETTE.box_border,
        padding=(0, 1),
        expand=True,
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


def _content_panel(index: _ReportIndex) -> Panel:
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
                inventory_table.add_row(label, Text(str(count), style="bold"))
        if inventory["status"] != "complete":
            inventory_table.add_row(
                "Inventory status", _terminal_text(inventory["status"])
            )
        renderables.append(inventory_table)

    source_rows = _source_rows(index)
    if source_rows:
        source_table = Table(box=None, pad_edge=False, expand=True)
        source_table.add_column("Source content", style="bold")
        source_table.add_column("In report", justify="right")
        source_table.add_column("Analyzed", justify="right")
        source_table.add_column("Not fully analyzed", justify="right")
        for label, found, analyzed, incomplete in source_rows:
            source_table.add_row(
                label,
                Text(str(found), style="bold"),
                Text(str(analyzed), style="bold"),
                Text(str(incomplete), style="bold"),
            )
        renderables.append(source_table)

    if not renderables:
        renderables.append(
            Text(
                "No archive entries or source-analysis records are saved in the report."
            )
        )
    return Panel(
        _spaced_group(renderables),
        title=Text("Report contents"),
        title_align="left",
        border_style=_PALETTE.box_border,
        padding=(0, 1),
        expand=True,
    )


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
    *,
    interactive: bool,
) -> Text | None:
    if shown >= total:
        return None
    noun = _count(total, unit).split(" ", 1)[1]
    line = Text(
        f"{noun.capitalize()} shown: {shown} of {total}. ",
        style="dim",
    )
    if interactive:
        line.append("Press a to show all saved detail for this artifact.", style="dim")
    elif report_path is not None:
        line.append("See all saved detail in ", style="dim")
        _append(line, report_path, f"not dim {_PALETTE.location}")
        line.append(" with --all.", style="dim")
    else:
        line.append(
            "For a later inspection, save JSON with check INPUT --json, then use "
            "view --all.",
            style="dim",
        )
    return line


def _coverage_panel(
    group: _CoverageGroup,
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
    interactive: bool,
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
    content.add_column("Affected location", overflow="fold")
    for record in shown:
        content.add_row(
            _record_format(record, index),
            _terminal_text(_location(record, index), _PALETTE.location),
        )
    if not records:
        artifact = cast(_Record, index.report["artifact"])
        content.add_row(
            _KIND_TITLES.get(cast(str, artifact["detected_kind"]), "Artifact"),
            _terminal_text(artifact["display_name"], _PALETTE.location),
        )

    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="bold", no_wrap=True)
    rows.add_column(ratio=1, overflow="fold")
    if group.scopes:
        rows.add_row("Affected checks", _joined_checks(group.scopes))
    explanation = _REASON_EXPLANATIONS.get(group.reason, group.message)
    if explanation is None:
        explanation = (
            "The report records this reason without an additional explanation."
        )
    rows.add_row("Result", _terminal_text(explanation))

    renderables: list[RenderableType] = [rows, content]
    detail = _detail_line(
        len(shown),
        len(records),
        "affected location",
        report_path,
        interactive=interactive,
    )
    if detail is not None:
        renderables.append(detail)
    title_style = (
        f"bold {_PALETTE.error}" if group.field_name == "failed_inputs" else "bold"
    )
    return Panel(
        _spaced_group(renderables),
        title=Text(title, style=title_style),
        title_align="left",
        border_style=_PALETTE.box_border,
        padding=(0, 1),
        expand=True,
    )


def _incomplete_section(
    groups: list[_CoverageGroup],
    index: _ReportIndex,
    field_name: str,
    *,
    report_path: Path | None,
    show_all: bool,
    interactive: bool,
) -> Group:
    matching = [
        group
        for group in groups
        if group.reason not in _LIMIT_REASON_NAMES and group.field_name == field_name
    ]
    return _spaced_group(
        [
            _coverage_panel(
                group,
                index,
                report_path=report_path,
                show_all=show_all,
                interactive=interactive,
            )
            for group in matching
        ]
    )


def _limit_sections(
    groups: list[_CoverageGroup],
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
    interactive: bool,
) -> Group:
    renderables: list[RenderableType] = []
    limits = cast(_Record, index.report["limits"])
    reached = cast(list[str], limits["reached"])
    effective = cast(_Record, limits["effective"])
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
        rows.add_column(style="bold", no_wrap=True)
        rows.add_column(ratio=1, overflow="fold")
        explanation = _LIMIT_EXPLANATIONS.get(limit)
        if explanation is not None:
            rows.add_row("Result", explanation)
        rows.add_row(
            "Effective limit",
            Text(
                _limit_value(limit, effective[limit]),
                style=f"bold {_PALETTE.attention}",
            ),
        )
        if scopes:
            rows.add_row("Affected checks", _joined_checks(scopes))
        if shown:
            locations = Text()
            for position, record in enumerate(shown):
                if position:
                    locations.append("\n")
                locations.append_text(
                    _terminal_text(_location(record, index), _PALETTE.location)
                )
            rows.add_row("Affected locations", locations)
        else:
            rows.add_row(
                "Affected locations",
                "No specific affected location is saved for this limit.",
            )

        content: list[RenderableType] = [rows]
        detail = _detail_line(
            len(shown),
            len(records),
            "affected location",
            report_path,
            interactive=interactive,
        )
        if detail is not None:
            content.append(detail)
        renderables.append(
            Panel(
                _spaced_group(content),
                title=Text(_LIMIT_TITLES.get(limit, limit), style=_PALETTE.attention),
                title_align="left",
                border_style=_PALETTE.box_border,
                padding=(0, 1),
                expand=True,
            )
        )
    return _spaced_group(renderables)


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
    group: _ObservationGroup,
    subjects: list[_SubjectGroup],
    index: _ReportIndex,
) -> Text:
    locations = {
        _location_key(observation, index) for observation in group.observations
    }
    line = Text()
    metrics = [
        (len(group.observations), "occurrence", "occurrences"),
        (len(locations), "location", "locations"),
    ]
    if group.rule_id == "python.dependencies":
        metrics.append((len(subjects), "name", "names"))
    for position, (count, singular, plural) in enumerate(metrics):
        if position:
            line.append("  ·  ", style="dim")
        line.append(str(count), style="bold")
        line.append(f" {singular if count == 1 else plural}")
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
    table.add_column("Evidence", ratio=2, overflow="fold")
    table.add_column("Occurrences", justify="right", no_wrap=True)
    table.add_column("Location", ratio=3, overflow="fold")
    displayed_locations: set[tuple[object, ...]] = set()

    for subject in selected_subjects:
        observations = subject.observations if show_all else subject.observations[:1]
        for position, observation in enumerate(observations):
            label = subject.label if position == 0 else ""
            table.add_row(
                _terminal_text(label),
                (
                    Text(str(len(subject.observations)), style="bold")
                    if position == 0
                    else ""
                ),
                _terminal_text(_location(observation, index), _PALETTE.location),
            )
            displayed_locations.add(_location_key(observation, index))
            snippet = observation.get("snippet")
            if (
                group.rule_id == "python.dependencies"
                and snippet is not None
                and str(snippet) != str(subject.label)
            ):
                context = Text("Context  ", style="dim")
                _append(context, snippet)
                table.add_row(context, "", "")
            evidence_records = [
                evidence
                for evidence in _linked_evidence(observation, index)
                if _useful_evidence(evidence, observation, group, index)
            ]
            if not show_all:
                evidence_records = evidence_records[:1]
            for evidence in evidence_records:
                relation = Text("Related  ", style="dim")
                relation.append(str(evidence["kind"]).replace("_", " "), style="dim")
                relation.append(" = ", style="dim")
                _append(relation, evidence["value"])
                table.add_row(
                    relation,
                    "",
                    _terminal_text(_location(evidence, index), _PALETTE.location),
                )
    return table, len(selected_subjects), len(displayed_locations)


def _observation_detail_line(
    group: _ObservationGroup,
    subjects: list[_SubjectGroup],
    displayed_subjects: int,
    displayed_locations: int,
    index: _ReportIndex,
    report_path: Path | None,
    *,
    interactive: bool,
) -> Text | None:
    locations = {
        _location_key(observation, index) for observation in group.observations
    }
    if displayed_subjects == len(subjects) and displayed_locations == len(locations):
        return None
    subject_unit = "name" if group.rule_id == "python.dependencies" else "item"
    line = Text(
        f"Saved {subject_unit}s shown: {displayed_subjects} of {len(subjects)}. "
        f"Locations shown: {displayed_locations} of {len(locations)}. ",
        style="dim",
    )
    if interactive:
        line.append("Press a to show all saved detail for this artifact.", style="dim")
    elif report_path is not None:
        line.append("See all saved detail in ", style="dim")
        _append(line, report_path, f"not dim {_PALETTE.location}")
        line.append(" with --all.", style="dim")
    else:
        line.append(
            "For a later inspection, save JSON with check INPUT --json, then use "
            "view --all.",
            style="dim",
        )
    return line


def _observation_body(
    group: _ObservationGroup,
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
    interactive: bool,
) -> Group:
    subjects = _subject_groups(group, index)
    table, displayed_subjects, displayed_locations = _observation_table(
        group,
        subjects,
        index,
        show_all=show_all,
    )
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold", no_wrap=True)
    summary.add_column(ratio=1, overflow="fold")
    summary.add_row("Saved", _summary_line(group, subjects, index))

    renderables: list[RenderableType] = [summary, table]
    detail = _observation_detail_line(
        group,
        subjects,
        displayed_subjects,
        displayed_locations,
        index,
        report_path,
        interactive=interactive,
    )
    if detail is not None:
        renderables.append(detail)
    shortened = sum(
        bool(observation["snippet_truncated"]) for observation in group.observations
    )
    if shortened:
        renderables.append(
            Text(
                f"{_count(shortened, 'saved snippet')} shortened by the report "
                "limit. Expansion cannot restore the omitted snippet text.",
                style="dim",
            )
        )
    return _spaced_group(renderables)


def _finding_groups(index: _ReportIndex) -> list[_ObservationGroup]:
    groups: list[_ObservationGroup] = []
    for rule_id in ("archive.structure", "python.absolute-path"):
        observations = [
            observation
            for observation in index.observations_by_rule.get(rule_id, [])
            if observation["kind"] == "finding"
        ]
        groups.extend(_observation_groups(observations, rule_id))
    return groups


def _finding_sections(
    groups: list[_ObservationGroup],
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
    interactive: bool,
) -> Group:
    renderables: list[RenderableType] = []
    for group in groups:
        body: list[RenderableType] = []
        explanation = _FINDING_EXPLANATIONS.get(group.rule_id)
        if explanation is not None:
            body.append(Text(explanation))
        body.append(
            _observation_body(
                group,
                index,
                report_path=report_path,
                show_all=show_all,
                interactive=interactive,
            )
        )
        renderables.append(
            Panel(
                _spaced_group(body),
                title=Text(
                    _CONDITION_TITLES.get(
                        group.condition_code, _humanize_code(group.condition_code)
                    )
                ),
                title_align="left",
                border_style=_PALETTE.box_border,
                padding=(0, 1),
                expand=True,
            )
        )
    return _spaced_group(renderables)


def _review_categories(
    index: _ReportIndex,
) -> list[tuple[_ReviewSpec, list[_ObservationGroup]]]:
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
    return categories


def _review_sections(
    categories: list[tuple[_ReviewSpec, list[_ObservationGroup]]],
    index: _ReportIndex,
    *,
    report_path: Path | None,
    show_all: bool,
    interactive: bool,
) -> Group:
    renderables: list[RenderableType] = []
    for spec, groups in categories:
        action = Table.grid(padding=(0, 2))
        action.add_column(style=f"bold {_PALETTE.attention}", no_wrap=True)
        action.add_column(ratio=1, overflow="fold")
        action.add_row("Question", spec.action)
        content: list[RenderableType] = [action]
        for group in groups:
            if spec.rule_id == "python.dependencies" or len(groups) > 1:
                content.append(
                    Text(
                        _CONDITION_TITLES.get(
                            group.condition_code,
                            _humanize_code(group.condition_code),
                        ),
                        style="bold",
                    )
                )
            content.append(
                _observation_body(
                    group,
                    index,
                    report_path=report_path,
                    show_all=show_all,
                    interactive=interactive,
                )
            )
        renderables.append(
            Panel(
                _spaced_group(content),
                title=Text(spec.title),
                title_align="left",
                border_style=_PALETTE.box_border,
                padding=(0, 1),
                expand=True,
            )
        )
    return _spaced_group(renderables)


def _measurements(rows: list[tuple[str, object]]) -> Table:
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style="bold", no_wrap=True)
    table.add_column(ratio=1, overflow="fold")
    for label, value in rows:
        rendered: RenderableType
        if isinstance(value, int):
            rendered = Text(str(value), style="bold")
        elif isinstance(value, (Text, Group, Table)):
            rendered = value
        else:
            rendered = _terminal_text(value)
        table.add_row(label, rendered)
    return table


def _report_reference(
    report_path: Path,
    *,
    interactive: bool,
    show_all: bool,
) -> Panel:
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style="bold", no_wrap=True)
    rows.add_column(ratio=1, overflow="fold")
    rows.add_row("Path", _terminal_text(report_path, _PALETTE.location))
    if interactive:
        instruction = (
            "Press a to return to compact detail for this artifact."
            if show_all
            else "Press a to show all saved detail for this artifact."
        )
    else:
        instruction = "Add --all to the view command."
    rows.add_row("More detail", instruction)
    return Panel(
        rows,
        title=Text("Saved report reference"),
        title_align="left",
        border_style=_PALETTE.box_border,
        padding=(0, 1),
        expand=True,
    )


class ReportPresentation:
    """Interpret one admitted checker document for plain and interactive output."""

    def __init__(
        self,
        report: dict[str, object],
        *,
        report_path: Path | None = None,
        interactive: bool = False,
    ) -> None:
        self.report_path = report_path
        self.interactive = interactive
        self._index = _index_report(cast(_Record, report))
        self._coverage = _coverage_groups(self._index)
        self._findings = _finding_groups(self._index)
        self._reviews = _review_categories(self._index)

    @property
    def artifact_name(self) -> str:
        artifact = cast(_Record, self._index.report["artifact"])
        return cast(str, artifact["display_name"])

    @staticmethod
    def section_spec(key: SectionKey) -> SectionSpec:
        return _SECTION_BY_KEY[key]

    def _ordinary_coverage(self, field_name: str) -> list[_CoverageGroup]:
        return [
            group
            for group in self._coverage
            if group.field_name == field_name
            and group.reason not in _LIMIT_REASON_NAMES
        ]

    def _coverage_measurements(
        self,
        groups: list[_CoverageGroup],
    ) -> Table:
        locations = {
            _location_key(record, self._index)
            for group in groups
            for record in group.records
        }
        return _measurements(
            [
                ("Recorded reasons", len(groups)),
                ("Affected locations", len(locations)),
            ]
        )

    def _rule_status_text(self, rule_ids: tuple[str, ...]) -> Text | None:
        by_rule = {
            cast(str, result["rule_id"]): cast(str, result["status"])
            for result in self._index.results
        }
        if rule_ids and all(by_rule.get(rule_id) == "complete" for rule_id in rule_ids):
            return Text("The contributing checks are recorded as complete.")

        labels = {
            "error": "recorded inspection errors",
            "partial": "partial recorded coverage",
            "unsupported": "unsupported input",
            "not_applicable": "not applicable",
            "absent": "no recorded result",
        }
        clauses: list[str] = []
        for status in ("error", "partial", "unsupported", "not_applicable", "absent"):
            matching = [
                _RULE_TITLES.get(rule_id, rule_id)
                for rule_id in rule_ids
                if by_rule.get(rule_id, "absent") == status
            ]
            if matching:
                clauses.append(f"{', '.join(matching)}: {labels[status]}.")
        return Text(" ".join(clauses)) if clauses else None

    def _report_qualifiers(self) -> list[str]:
        qualifiers: list[str] = []
        artifact = cast(_Record, self._index.report["artifact"])
        inventory = cast(_Record, self._index.report["inventory"])
        limits = cast(_Record, self._index.report["limits"])
        reached = set(cast(list[str], limits["reached"]))
        if not artifact["snapshot_complete"]:
            qualifiers.append("The input snapshot is incomplete.")
        inventory_status = cast(str, inventory["status"])
        inventory_clauses = {
            "partial": "The saved inventory is partial.",
            "error": "The inventory records an error.",
            "unsupported": "The input format is unsupported for inventory.",
        }
        if inventory_status in inventory_clauses:
            qualifiers.append(inventory_clauses[inventory_status])
        if reached - {"max_observations", "max_report_bytes"}:
            qualifiers.append("Recorded limits affected inspection.")
        if reached & {"max_observations", "max_report_bytes"}:
            qualifiers.append(
                "Recorded limits restricted saved detail and may also have stopped "
                "inspection."
            )
        return qualifiers

    def _artifact_summary(self) -> Text:
        artifact = cast(_Record, self._index.report["artifact"])
        kind = cast(str, artifact["detected_kind"])
        clauses = [
            (
                "The input snapshot is complete."
                if artifact["snapshot_complete"]
                else (
                    "The input snapshot is incomplete; the recorded size does not "
                    "establish a complete copy."
                )
            )
        ]
        if kind.startswith("unsupported_"):
            clauses.append("This top-level format is not supported by checker v1.")
        elif kind == "unclassified":
            clauses.append("The report does not classify the input format.")
        return Text(" ".join(clauses))

    def _content_summary(self) -> Text:
        inventory = cast(_Record, self._index.report["inventory"])
        clauses = ["Counts describe saved report records."]
        if inventory["status"] == "complete":
            clauses.append("The inventory is recorded as complete.")
        clauses.extend(self._report_qualifiers())
        return Text(" ".join(dict.fromkeys(clauses)))

    def _incomplete_summary(self, key: SectionKey) -> RenderableType:
        field_name = "skipped_inputs" if key == "skipped" else "failed_inputs"
        groups = self._ordinary_coverage(field_name)
        if not groups:
            base = (
                "No skipped-input records are shown on this page."
                if key == "skipped"
                else "No failed-input records are shown on this page."
            )
            clauses = [base]
        else:
            clauses = []
        limit_groups = [
            group
            for group in self._coverage
            if group.field_name == field_name and group.reason in _LIMIT_REASON_NAMES
        ]
        reached = set(
            cast(list[str], cast(_Record, self._index.report["limits"])["reached"])
        )
        if limit_groups:
            if any(
                not any(
                    group.reason in _LIMIT_REASONS.get(limit, set())
                    for limit in reached
                )
                for group in limit_groups
            ):
                clauses.append(
                    "Limit-related coverage records exist without a matching "
                    "reached-limit entry."
                )
            else:
                clauses.append("Limit-related gaps are listed under Limits reached.")
        if key == "skipped" and any(
            group.reason.startswith("unsupported") for group in groups
        ):
            clauses.append("Unsupported content is included here as an inspection gap.")
        if (
            key == "failed"
            and groups
            and any(
                observation["condition_code"] != "resource_limit_reached"
                for observations in self._index.observations_by_rule.values()
                for observation in observations
            )
        ):
            clauses.append("Other saved observations remain available.")
        qualifiers = self._report_qualifiers()
        clauses.extend(qualifiers)
        text = Text(" ".join(dict.fromkeys(clauses))) if clauses else None
        if not groups:
            assert text is not None
            return text
        parts: list[RenderableType] = [self._coverage_measurements(groups)]
        if text is not None:
            parts.append(text)
        return _spaced_group(parts)

    def _limits_summary(self) -> RenderableType:
        limits = cast(_Record, self._index.report["limits"])
        reached = cast(list[str], limits["reached"])
        unmatched = [
            group
            for group in self._coverage
            if group.reason in _LIMIT_REASON_NAMES
            and not any(
                group.reason in _LIMIT_REASONS.get(limit, set()) for limit in reached
            )
        ]
        if not reached:
            message = "No reached limits are recorded."
            if unmatched:
                message += (
                    " Limit-related coverage records exist without a matching "
                    "reached-limit entry."
                )
            return Text(message)
        titles = Text()
        for position, limit in enumerate(reached):
            if position:
                titles.append(", ")
            titles.append(_LIMIT_TITLES.get(limit, limit), style=_PALETTE.attention)
        rows: list[tuple[str, object]] = [
            ("Reached limits", len(reached)),
            ("Recorded", titles),
        ]
        clauses: list[str] = []
        if set(reached) - {"max_observations", "max_report_bytes"}:
            clauses.append("These bounds restricted inspection.")
        if set(reached) & {"max_observations", "max_report_bytes"}:
            clauses.append(
                "Saved detail is bounded; expansion cannot recover omitted records."
            )
        if unmatched:
            clauses.append(
                "Limit-related coverage records exist without a matching "
                "reached-limit entry."
            )
        parts: list[RenderableType] = [_measurements(rows)]
        if clauses:
            parts.append(Text(" ".join(clauses)))
        return _spaced_group(parts)

    def _findings_summary(self) -> RenderableType:
        total = sum(len(group.observations) for group in self._findings)
        parts: list[RenderableType] = []
        if self._findings:
            parts.append(
                _measurements(
                    [
                        ("Artifact findings", total),
                        ("Condition groups", len(self._findings)),
                    ]
                )
            )
        else:
            parts.append(
                Text(
                    "No artifact findings are reported. This is not a correctness "
                    "or reproducibility verdict."
                )
            )
        status = self._rule_status_text(("archive.structure", "python.absolute-path"))
        if status is not None:
            parts.append(status)
        parts.extend(Text(clause) for clause in self._report_qualifiers())
        return _spaced_group(parts)

    def _review_summary(self) -> RenderableType:
        total = sum(
            len(group.observations)
            for _review_spec, groups in self._reviews
            for group in groups
        )
        parts: list[RenderableType] = []
        if self._reviews:
            parts.append(
                _measurements(
                    [
                        ("Review items", total),
                        ("Categories", len(self._reviews)),
                    ]
                )
            )
        else:
            parts.append(
                Text(
                    "No review items are reported. This does not establish that no "
                    "human review is needed."
                )
            )
        status = self._rule_status_text(
            tuple(review_spec.rule_id for review_spec in _REVIEW_SPECS)
        )
        if status is not None:
            parts.append(status)
        parts.extend(Text(clause) for clause in self._report_qualifiers())
        return _spaced_group(parts)

    def _saved_summary(self) -> Text:
        if self.report_path is None:
            line = Text("No saved-report path was supplied to this view. ")
        else:
            line = Text("This view reads the saved report at ")
            line.append_text(_terminal_text(self.report_path, _PALETTE.location))
            line.append(", not the original input. ")
        line.append(
            "Expansion shows only saved detail; it does not repeat inspection or "
            "recover omitted content."
        )
        return line

    def _summary(self, key: SectionKey) -> RenderableType:
        if key == "artifact":
            return self._artifact_summary()
        if key == "content":
            return self._content_summary()
        if key in {"skipped", "failed"}:
            return self._incomplete_summary(key)
        if key == "limits":
            return self._limits_summary()
        if key == "findings":
            return self._findings_summary()
        if key == "review":
            return self._review_summary()
        return self._saved_summary()

    def _body(self, key: SectionKey, *, show_all: bool) -> list[RenderableType]:
        if key == "artifact":
            return [_identity_panel(self._index)]
        if key == "content":
            return [_content_panel(self._index)]
        if key in {"skipped", "failed"}:
            field_name = "skipped_inputs" if key == "skipped" else "failed_inputs"
            return [
                _incomplete_section(
                    self._coverage,
                    self._index,
                    field_name,
                    report_path=self.report_path,
                    show_all=show_all,
                    interactive=self.interactive,
                )
            ]
        if key == "limits":
            return [
                _limit_sections(
                    self._coverage,
                    self._index,
                    report_path=self.report_path,
                    show_all=show_all,
                    interactive=self.interactive,
                )
            ]
        if key == "findings":
            return [
                _finding_sections(
                    self._findings,
                    self._index,
                    report_path=self.report_path,
                    show_all=show_all,
                    interactive=self.interactive,
                )
            ]
        if key == "review":
            return [
                _review_sections(
                    self._reviews,
                    self._index,
                    report_path=self.report_path,
                    show_all=show_all,
                    interactive=self.interactive,
                )
            ]
        if self.report_path is None:
            return []
        return [
            _report_reference(
                self.report_path,
                interactive=self.interactive,
                show_all=show_all,
            )
        ]

    def render_section(
        self,
        key: SectionKey,
        *,
        show_all: bool = False,
    ) -> Group:
        """Render one stable report section without a page heading."""

        spec = self.section_spec(key)
        return _spaced_group(
            [
                Text(spec.meaning),
                _summary_block(self._summary(key)),
                *self._body(key, show_all=show_all),
            ]
        )


def build_report(
    report: dict[str, object],
    *,
    report_path: Path | None = None,
    show_all: bool = False,
) -> Group:
    """Build one completed report from the same sections used by the viewer."""

    presentation = ReportPresentation(report, report_path=report_path)
    return _spaced_group(
        [
            _spaced_group(
                [
                    _section_heading(spec),
                    presentation.render_section(spec.key, show_all=show_all),
                ]
            )
            for spec in SECTION_SPECS
        ]
    )


def render_report(
    report: dict[str, object],
    console: Console,
    *,
    report_path: Path | None = None,
    show_all: bool = False,
) -> None:
    """Render one completed schema document without inspecting its source."""

    console.print(build_report(report, report_path=report_path, show_all=show_all))

"""Bounded, topic-first Rich rendering for static checker reports."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import TextIO, cast

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_Record = dict[str, object]
_EXAMPLE_LIMIT = 3
_BIDI_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}
_STATUS_ORDER = ("complete", "partial", "unsupported", "error", "not_applicable")


@dataclass(frozen=True, slots=True)
class _Palette:
    identity_border: str
    identity_accent: str
    limitation_band: str
    overview_band: str
    finding_band: str
    review_band: str
    metadata: str


_PALETTE = _Palette(
    identity_border="#915f7d",
    identity_accent="#b87755",
    limitation_band="#261f28 on #d6c4d0",
    overview_band="bright_white on #493744",
    finding_band="bright_white on #8a3f2c",
    review_band="#23190f on #d98a4c",
    metadata="#887984",
)

_RULE_TITLES = {
    "archive.structure": "Archive structure",
    "python.absolute-path": "Absolute path literals",
    "python.dependencies": "Python dependencies",
    "python.sys-path-three-dot": "Three-dot sys.path segments",
    "python.download-comment-http-url": "Download comments containing HTTP URLs",
    "python.open-bundled-archive-member": "Reads from bundled archives",
    "python.pandas-csv-inventory-absence": "Pandas CSV reads absent from inventory",
    "python.notebook-pip-install": "Notebook pip install commands",
    "python.gdown-anonymized-value": "Anonymized gdown identifiers",
    "python.entry-point-input": "Entry-point standard input",
    "python.gfile-bucket-authority": "GFile bucket authorities",
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
    "resource_limit_reached": "Resource limit reached",
    "windows_device_path": "Windows device path literals",
    "windows_unc_path": "Windows UNC path literals",
    "windows_drive_path": "Windows drive path literals",
    "posix_absolute_path": "POSIX absolute path literals",
    "tilde_path": "Home-relative path literals",
    "import_without_exact_declaration": "Imports without exact dependency declarations",
    "declaration_without_exact_import": "Dependency declarations without exact imports",
    "three_dot_path_segment": "Three-dot sys.path segments",
    "download_comment_with_http_url": "Download comments containing HTTP URLs",
    "read_path_only_in_bundled_archive": "Read paths found only in bundled archives",
    "pandas_csv_not_in_inventory": "Pandas CSV paths absent from inventory",
    "notebook_pip_install": "Notebook pip install commands",
    "anonymized_download_identifier": "Anonymized gdown identifiers",
    "entry_point_stdin": "Entry-point standard input",
    "bucket_authority_literal": "Literal GFile bucket authority",
}
_RULE_EXPLANATIONS = {
    "archive.structure": (
        "This finding describes archive metadata or a bounded archive read; it does "
        "not assert identical behavior on every filesystem."
    ),
    "python.absolute-path": (
        "This lexical path form appears in a supported call; the checker does not "
        "inspect the host filesystem or infer call reachability."
    ),
    "python.dependencies": (
        "Exact lexical import and declaration evidence differs here. Interpretation "
        "is needed; this does not establish a missing or unused dependency."
    ),
    "python.sys-path-three-dot": (
        "Interpret whether the exact three-dot segment is intentional or an "
        "artifact-specific path assumption."
    ),
    "python.download-comment-http-url": (
        "Interpret whether the comment describes a required manual acquisition "
        "step, an optional operation, or background information."
    ),
    "python.open-bundled-archive-member": (
        "The static relationship does not establish extraction, mounting, call "
        "reachability, or the effect of earlier calls."
    ),
    "python.pandas-csv-inventory-absence": (
        "Interpret whether another documented step, mount, generation process, or "
        "runtime environment supplies this path."
    ),
    "python.notebook-pip-install": (
        "Interpret whether the command is required setup, an optional convenience, "
        "or historical material; the checker did not run it."
    ),
    "python.gdown-anonymized-value": (
        "Interpret whether anonymization is intentional or another release step "
        "supplies a concrete identifier."
    ),
    "python.entry-point-input": (
        "Interpret how standard input is supplied; redirected or piped input may be "
        "valid and no interactive requirement is inferred."
    ),
    "python.gfile-bucket-authority": (
        "Interpret whether the legal authority “bucket” names a real resource or an "
        "artifact-specific value."
    ),
}
_CONDITION_EXPLANATIONS = {
    "resource_limit_reached": (
        "A fixed inspection limit prevented complete coverage. The limitation "
        "section names the affected scope and retained boundary."
    ),
}
_LIMIT_TITLES = {
    "max_input_bytes": "Maximum input size reached",
    "max_member_count": "Maximum archive member count reached",
    "max_nested_zip_depth": "Maximum nested ZIP depth reached",
    "max_expanded_bytes_per_member": "Maximum expanded member size reached",
    "max_expanded_bytes_total": "Maximum total expanded size reached",
    "max_python_source_bytes": "Maximum Python source size reached",
    "max_notebook_bytes": "Maximum notebook size reached",
    "max_dependency_file_bytes": "Maximum dependency-file size reached",
    "max_worker_rss_bytes": "Maximum worker memory reached",
    "max_temporary_bytes": "Maximum temporary storage reached",
    "max_elapsed_seconds": "Maximum inspection time reached",
    "max_observations": "Maximum observation count reached",
    "max_report_bytes": "Maximum report size reached",
}


@dataclass(slots=True)
class _ObservationGroup:
    rule_id: str
    kind: str
    condition_code: str
    count: int = 0
    examples: list[_Record] = field(default_factory=list)


@dataclass(slots=True)
class _CoverageGroup:
    field_name: str
    reason: str
    records: list[_Record]
    message: object
    scopes: list[tuple[str, str]] = field(default_factory=list)


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


def _humanize(value: object) -> str:
    replacements = {
        "csv": "CSV",
        "gfile": "GFile",
        "http": "HTTP",
        "python": "Python",
        "rss": "RSS",
        "zip": "ZIP",
    }
    words = str(value).replace("_", " ").strip().split()
    normalized = [replacements.get(word.casefold(), word) for word in words]
    if normalized and normalized[0] not in replacements.values():
        normalized[0] = normalized[0].capitalize()
    return " ".join(normalized)


def _count(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _print_band(console: Console, title: str, style: str) -> None:
    console.print()
    band = Table.grid(expand=True, padding=(0, 1))
    band.add_row(Text(title, style="bold"), style=style)
    console.print(band)


def _append(line: Text, value: object, style: str | None = None) -> None:
    line.append_text(_terminal_text(value, style))


def _member_maps(
    report: _Record,
) -> tuple[dict[str, _Record], dict[str, _Record]]:
    inventory = cast(_Record, report["inventory"])
    members = {
        cast(str, member["member_id"]): member
        for member in cast(list[_Record], inventory["members"])
    }
    sources = {
        cast(str, source["source_id"]): source
        for source in cast(list[_Record], report["source_index"])
    }
    return members, sources


def _member_name(member: _Record) -> str:
    name = cast(str, member["name"])
    ordinal = cast(int, member["duplicate_ordinal"])
    return name if ordinal == 1 else f"{name} (duplicate {ordinal})"


def _location(
    item: _Record,
    report: _Record,
    members: dict[str, _Record],
    sources: dict[str, _Record],
) -> str:
    source_id = cast(str | None, item.get("source_id"))
    source = sources.get(source_id) if source_id is not None else None
    member_id = cast(str | None, item.get("member_id"))
    if member_id is None and source is not None:
        member_id = cast(str | None, source.get("member_id"))

    if member_id is not None and member_id in members:
        parts = [_member_name(members[member_id])]
    else:
        artifact = cast(_Record, report["artifact"])
        parts = [cast(str, artifact["display_name"])]

    cell = item.get("cell")
    if cell is None and source is not None:
        cell = source.get("cell")
    if cell is not None:
        parts.append(f"cell {cell}")
    if item.get("line") is not None:
        parts.append(f"line {item['line']}")
    return " · ".join(parts)


def _observation_groups(report: _Record) -> list[_ObservationGroup]:
    groups: dict[tuple[str, str, str], _ObservationGroup] = {}
    for result in cast(list[_Record], report["rule_results"]):
        rule_id = cast(str, result["rule_id"])
        for observation in cast(list[_Record], result["observations"]):
            kind = cast(str, observation["kind"])
            condition_code = cast(str, observation["condition_code"])
            key = (rule_id, kind, condition_code)
            group = groups.get(key)
            if group is None:
                group = _ObservationGroup(rule_id, kind, condition_code)
                groups[key] = group
            group.count += 1
            if len(group.examples) < _EXAMPLE_LIMIT:
                group.examples.append(observation)
    return list(groups.values())


def _evidence_for_examples(
    report: _Record,
    groups: list[_ObservationGroup],
) -> dict[str, _Record]:
    wanted = {
        cast(str, evidence_id)
        for group in groups
        for example in group.examples
        for evidence_id in cast(list[str], example["evidence_ids"])
    }
    if not wanted:
        return {}

    selected: dict[str, _Record] = {}
    for result in cast(list[_Record], report["rule_results"]):
        for evidence in cast(list[_Record], result["evidence"]):
            evidence_id = cast(str, evidence["evidence_id"])
            if evidence_id in wanted:
                selected[evidence_id] = evidence
    return selected


def _duplicates_observation(evidence: _Record, observation: _Record) -> bool:
    return (
        evidence.get("value") == observation.get("snippet")
        and evidence.get("member_id") == observation.get("member_id")
        and evidence.get("source_id") == observation.get("source_id")
        and evidence.get("cell") == observation.get("cell")
        and evidence.get("line") == observation.get("line")
    )


def _snippet_repeats_location(
    observation: _Record,
    members: dict[str, _Record],
) -> bool:
    member_id = cast(str | None, observation.get("member_id"))
    member = members.get(member_id) if member_id is not None else None
    return (
        member is not None
        and observation.get("source_id") is None
        and observation.get("cell") is None
        and observation.get("line") is None
        and observation.get("snippet") == member.get("name")
    )


def _print_identity(console: Console, report: _Record, palette: _Palette) -> None:
    artifact = cast(_Record, report["artifact"])
    inventory = cast(_Record, report["inventory"])
    title = _terminal_text(artifact["display_name"], "bold")
    body = Text("ReproReady static check", style=f"bold {palette.identity_accent}")
    body.append(" · ")
    _append(body, _humanize(artifact["detected_kind"]))
    body.append("\n")
    body.append(
        f"{_count(len(cast(list[object], inventory['members'])), 'inventory member')} · "
        f"{_count(len(cast(list[object], report['source_index'])), 'source record')}"
    )
    console.print(
        Panel(
            body,
            title=title,
            title_align="left",
            border_style=palette.identity_border,
            expand=True,
        )
    )


def _has_limitations(report: _Record) -> bool:
    inventory = cast(_Record, report["inventory"])
    if inventory["status"] != "complete" or inventory["issues"]:
        return True
    limits = cast(_Record, report["limits"])
    if limits["reached"]:
        return True
    if any(
        source["status"] != "inspected"
        for source in cast(list[_Record], report["source_index"])
    ):
        return True
    return any(
        result["status"] in {"partial", "unsupported", "error"}
        for result in cast(list[_Record], report["rule_results"])
    )


def _print_record_examples(
    console: Console,
    records: list[_Record],
    report: _Record,
    members: dict[str, _Record],
    sources: dict[str, _Record],
) -> None:
    for record in records[:_EXAMPLE_LIMIT]:
        line = Text("  • ", style="dim")
        _append(line, _location(record, report, members, sources))
        console.print(line)
    if len(records) > _EXAMPLE_LIMIT:
        omitted = len(records) - _EXAMPLE_LIMIT
        console.print(
            Text(
                f"  Showing {_EXAMPLE_LIMIT} of {len(records)} inputs; "
                f"{omitted} omitted from this view. Use --json for the complete "
                "retained record.",
                style="dim",
            )
        )


def _coverage_groups(report: _Record) -> list[_CoverageGroup]:
    groups: dict[
        tuple[str, str, tuple[tuple[object, object], ...]],
        _CoverageGroup,
    ] = {}
    for result in cast(list[_Record], report["rule_results"]):
        rule_id = cast(str, result["rule_id"])
        status = cast(str, result["status"])
        if status not in {"partial", "unsupported", "error"}:
            continue
        for field_name in ("skipped_inputs", "failed_inputs"):
            by_reason: dict[str, list[_Record]] = {}
            for record in cast(list[_Record], result[field_name]):
                by_reason.setdefault(cast(str, record["reason_code"]), []).append(
                    record
                )
            for reason, records in by_reason.items():
                locations = tuple(
                    (record.get("member_id"), record.get("source_id"))
                    for record in records
                )
                key = (field_name, reason, locations)
                group = groups.get(key)
                if group is None:
                    group = _CoverageGroup(
                        field_name=field_name,
                        reason=reason,
                        records=records,
                        message=records[0]["message"],
                    )
                    groups[key] = group
                group.scopes.append((rule_id, status))
    return list(groups.values())


def _record_locations(records: list[_Record]) -> tuple[tuple[object, object], ...]:
    return tuple(
        (record.get("member_id"), record.get("source_id")) for record in records
    )


def _matching_coverage_groups(
    reason: str,
    records: list[_Record],
    coverage_groups: list[_CoverageGroup],
    consumed: set[int],
) -> list[tuple[int, _CoverageGroup]]:
    locations = _record_locations(records)
    return [
        (index, group)
        for index, group in enumerate(coverage_groups)
        if index not in consumed
        and group.reason == reason
        and _record_locations(group.records) == locations
    ]


def _print_limitation_group(
    console: Console,
    *,
    reason: str,
    records: list[_Record],
    source_summary: str | None,
    coverage_groups: list[_CoverageGroup],
    report: _Record,
    palette: _Palette,
    members: dict[str, _Record],
    sources: dict[str, _Record],
) -> None:
    console.print(Text(_humanize(reason), style="bold"))
    if source_summary is not None:
        console.print(f"  {source_summary}")

    for group in coverage_groups:
        action = "skipped" if group.field_name == "skipped_inputs" else "failed"
        coverage = (
            f"{_count(len(group.records), f'{action} input')} in "
            f"{_count(len(group.scopes), 'rule')}"
        )
        if len(group.scopes) > 1:
            coverage = (
                f"{_count(len(group.records), f'{action} input')} in each of "
                f"{_count(len(group.scopes), 'rule')}"
            )
        console.print(f"  Rule coverage: {coverage}")
        scope_label = "Affected rule" if len(group.scopes) == 1 else "Affected rules"
        console.print(Text(f"  {scope_label}:", style="bold"))
        for rule_id, status in group.scopes:
            scope = Text("    • ")
            scope.append(_RULE_TITLES.get(rule_id, _humanize(rule_id)))
            scope.append(" (")
            _append(scope, rule_id, palette.metadata)
            scope.append(f"): {status}")
            console.print(scope)

    message = next(
        (record.get("message") for record in records if record.get("message")),
        coverage_groups[0].message if coverage_groups else None,
    )
    if message is not None:
        message_line = Text("  ")
        _append(message_line, message)
        console.print(message_line)

    example_records = records or coverage_groups[0].records
    _print_record_examples(console, example_records, report, members, sources)


def _print_limitations(
    console: Console,
    report: _Record,
    palette: _Palette,
    members: dict[str, _Record],
    sources: dict[str, _Record],
) -> None:
    _print_band(console, "Inspection limitations", palette.limitation_band)
    inventory = cast(_Record, report["inventory"])
    if inventory["status"] != "complete":
        console.print(f"Inventory coverage: {inventory['status']}")

    limits = cast(_Record, report["limits"])
    for limit in cast(list[str], limits["reached"]):
        console.print(Text(_LIMIT_TITLES.get(limit, _humanize(limit)), style="bold"))

    coverage_groups = _coverage_groups(report)
    consumed: set[int] = set()

    issue_groups: dict[str, list[_Record]] = {}
    for issue in cast(list[_Record], inventory["issues"]):
        issue_groups.setdefault(cast(str, issue["code"]), []).append(issue)
    for code, issues in issue_groups.items():
        matches = _matching_coverage_groups(
            code,
            issues,
            coverage_groups,
            consumed,
        )
        consumed.update(index for index, _ in matches)
        _print_limitation_group(
            console,
            reason=code,
            records=issues,
            source_summary=f"Inventory: {_count(len(issues), 'occurrence')}",
            coverage_groups=[group for _, group in matches],
            report=report,
            palette=palette,
            members=members,
            sources=sources,
        )

    source_groups: dict[tuple[str, str], list[_Record]] = {}
    for source in cast(list[_Record], report["source_index"]):
        if source["status"] == "inspected":
            continue
        key = (
            cast(str, source["status"]),
            cast(str, source["reason_code"] or "unspecified"),
        )
        source_groups.setdefault(key, []).append(source)
    for (status, reason), records in source_groups.items():
        matches = _matching_coverage_groups(
            reason,
            records,
            coverage_groups,
            consumed,
        )
        consumed.update(index for index, _ in matches)
        _print_limitation_group(
            console,
            reason=reason,
            records=records,
            source_summary=(
                f"Source inspection: {_count(len(records), 'source record')} {status}"
            ),
            coverage_groups=[group for _, group in matches],
            report=report,
            palette=palette,
            members=members,
            sources=sources,
        )

    for index, group in enumerate(coverage_groups):
        if index in consumed:
            continue
        _print_limitation_group(
            console,
            reason=group.reason,
            records=[],
            source_summary=None,
            coverage_groups=[group],
            report=report,
            palette=palette,
            members=members,
            sources=sources,
        )


def _print_overview(
    console: Console,
    report: _Record,
    groups: list[_ObservationGroup],
    palette: _Palette,
) -> None:
    finding_count = sum(group.count for group in groups if group.kind == "finding")
    review_groups = [group for group in groups if group.kind == "needs_human_review"]
    review_count = sum(group.count for group in review_groups)
    _print_band(console, "Overview", palette.overview_band)
    console.print(
        Text(
            f"{_count(finding_count, 'finding')} · "
            f"{_count(review_count, 'human-review observation')} · "
            f"{_count(len(review_groups), 'human-review topic')}"
        )
    )

    results = cast(list[_Record], report["rule_results"])
    statuses = Counter(cast(str, result["status"]) for result in results)
    status_text = " · ".join(
        _count(
            statuses[status],
            status.replace("_", " "),
            status.replace("_", " "),
        )
        for status in _STATUS_ORDER
        if statuses[status]
    )
    console.print(f"{len(results)} rules total: {status_text}")
    console.print(
        Text(
            "Rule status describes inspection coverage, not whether the artifact "
            "passes.",
            style="dim",
        )
    )
    console.print(
        Text(
            "Human-review observations require interpretation; they do not establish "
            "defects.",
            style="dim",
        )
    )
    console.print(
        Text(
            "JSON retains the complete technical record within reported limits: "
            "reproready check PATH --json",
            style="dim",
        )
    )
    if not groups:
        console.print("No findings or human-review observations were produced.")


def _print_observation_groups(
    console: Console,
    report: _Record,
    groups: list[_ObservationGroup],
    evidence: dict[str, _Record],
    palette: _Palette,
    members: dict[str, _Record],
    sources: dict[str, _Record],
) -> None:
    sections = (
        ("finding", "Findings", palette.finding_band),
        ("needs_human_review", "Human-review observations", palette.review_band),
    )
    for kind, section_title, band_style in sections:
        section_groups = [group for group in groups if group.kind == kind]
        if not section_groups:
            continue
        _print_band(console, section_title, band_style)
        for group in section_groups:
            title = _CONDITION_TITLES.get(
                group.condition_code, _humanize(group.condition_code)
            )
            heading = Text(title, style="bold")
            heading.append(f" · {_count(group.count, 'occurrence')} (")
            _append(heading, group.rule_id, palette.metadata)
            heading.append(")")
            console.print(heading)
            console.print(
                Text(
                    _CONDITION_EXPLANATIONS.get(
                        group.condition_code,
                        _RULE_EXPLANATIONS.get(
                            group.rule_id,
                            "This static observation should be interpreted in "
                            "artifact context.",
                        ),
                    )
                )
            )
            for example in group.examples:
                line = Text("  • ", style="dim")
                _append(line, _location(example, report, members, sources))
                if example["snippet"] is not None and not _snippet_repeats_location(
                    example, members
                ):
                    line.append(" — ")
                    _append(line, example["snippet"])
                console.print(line)

                shown = 0
                for evidence_id in cast(list[str], example["evidence_ids"]):
                    linked = evidence.get(evidence_id)
                    if linked is None or _duplicates_observation(linked, example):
                        continue
                    evidence_line = Text("      Supporting evidence: ", style="dim")
                    _append(evidence_line, _humanize(linked["kind"]), "dim")
                    evidence_line.append(" · ", style="dim")
                    _append(
                        evidence_line,
                        _location(linked, report, members, sources),
                        "dim",
                    )
                    evidence_line.append(" — ", style="dim")
                    _append(evidence_line, linked["value"], "dim")
                    console.print(evidence_line)
                    shown += 1
                    if shown == _EXAMPLE_LIMIT:
                        break
            if group.count > len(group.examples):
                omitted = group.count - len(group.examples)
                console.print(
                    Text(
                        f"Showing {len(group.examples)} of {group.count} observations; "
                        f"{omitted} omitted from this view. Use --json for the complete "
                        "retained record.",
                        style="dim",
                    )
                )
            console.print()


def render_report(report: dict[str, object], console: Console) -> None:
    """Render one completed schema document without inspecting its source."""

    record = cast(_Record, report)
    groups = _observation_groups(record)
    members, sources = _member_maps(record)
    evidence = _evidence_for_examples(record, groups)

    _print_identity(console, record, _PALETTE)
    if _has_limitations(record):
        _print_limitations(console, record, _PALETTE, members, sources)
    _print_overview(console, record, groups, _PALETTE)
    _print_observation_groups(
        console,
        record,
        groups,
        evidence,
        _PALETTE,
        members,
        sources,
    )

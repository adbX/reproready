"""Rich terminal rendering for internal checker reports."""

from __future__ import annotations

import os
from typing import TextIO

from rich.console import Console
from rich.text import Text

_BIDI_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}


def report_console(*, file: TextIO | None = None) -> Console:
    """Construct the internal report console with the standard color override."""

    return Console(file=file, no_color=os.environ.get("NO_COLOR") is not None)


def _terminal_text(value: object) -> Text:
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
    return Text("".join(escaped))


def _print_field(console: Console, label: str, value: object) -> None:
    line = Text(label)
    line.append_text(_terminal_text(value))
    console.print(line)


def _archive_target(member_id: object) -> str:
    return "archive" if member_id is None else str(member_id)


def _source_target(
    source_id: object,
    member_id: object,
    *,
    cell: object = None,
    line: object = None,
) -> str:
    parts: list[str] = []
    if source_id is not None:
        parts.append(str(source_id))
    if member_id is not None:
        parts.append(str(member_id))
    if cell is not None:
        parts.append(f"cell:{cell}")
    if line is not None:
        parts.append(f"line:{line}")
    return ", ".join(parts) if parts else "artifact"


def render_report(report: dict[str, object], console: Console) -> None:
    """Render implemented rule results without interpreting artifact markup."""

    artifact = report["artifact"]
    archive_result = report["rule_results"][0]
    path_result = report["rule_results"][1]
    dependency_result = report["rule_results"][2]
    _print_field(console, "Artifact: ", artifact["display_name"])
    _print_field(console, "Kind: ", artifact["detected_kind"])
    _print_field(console, "archive.structure: ", archive_result["status"])

    archive_observations = archive_result["observations"]
    if archive_result["status"] == "complete" and not archive_observations:
        console.print(Text("no finding in the checks run"))
    for observation in archive_observations:
        line = Text("Finding: ")
        line.append_text(_terminal_text(observation["condition_code"]))
        line.append(f" [{_archive_target(observation['member_id'])}]")
        if observation["snippet"] is not None:
            line.append(": ")
            line.append_text(_terminal_text(observation["snippet"]))
        console.print(line)
    for label, field in (
        ("Skipped: ", "skipped_inputs"),
        ("Failed: ", "failed_inputs"),
    ):
        for coverage in archive_result[field]:
            line = Text(label)
            line.append_text(_terminal_text(coverage["reason_code"]))
            line.append(f" [{_archive_target(coverage['member_id'])}]")
            console.print(line)

    _print_field(console, "python.absolute-path: ", path_result["status"])
    for observation in path_result["observations"]:
        line = Text("Finding: ")
        line.append_text(_terminal_text(observation["condition_code"]))
        target = _source_target(
            observation["source_id"],
            observation["member_id"],
            cell=observation["cell"],
            line=observation["line"],
        )
        line.append(f" [{target}]")
        if observation["snippet"] is not None:
            line.append(": ")
            line.append_text(_terminal_text(observation["snippet"]))
        console.print(line)
    for label, field in (
        ("Skipped: ", "skipped_inputs"),
        ("Failed: ", "failed_inputs"),
    ):
        for coverage in path_result[field]:
            line = Text(label)
            line.append_text(_terminal_text(coverage["reason_code"]))
            target = _source_target(
                coverage["source_id"],
                coverage["member_id"],
            )
            line.append(f" [{target}]")
            console.print(line)

    _print_field(console, "python.dependencies: ", dependency_result["status"])
    if (
        dependency_result["status"] == "complete"
        and not dependency_result["evidence"]
        and not dependency_result["observations"]
    ):
        console.print(Text("no finding in the checks run"))
    for evidence in dependency_result["evidence"]:
        line = Text("Evidence: ")
        line.append_text(_terminal_text(evidence["kind"]))
        line.append(" ")
        line.append_text(_terminal_text(evidence["evidence_id"]))
        target = _source_target(
            evidence["source_id"],
            evidence["member_id"],
            cell=evidence["cell"],
            line=evidence["line"],
        )
        line.append(f" [{target}]: ")
        line.append_text(_terminal_text(evidence["value"]))
        if evidence["related_evidence_ids"]:
            line.append(" (related: ")
            line.append_text(
                _terminal_text(", ".join(evidence["related_evidence_ids"]))
            )
            line.append(")")
        console.print(line)
    for observation in dependency_result["observations"]:
        label = (
            "Review: " if observation["kind"] == "needs_human_review" else "Finding: "
        )
        line = Text(label)
        line.append_text(_terminal_text(observation["condition_code"]))
        target = _source_target(
            observation["source_id"],
            observation["member_id"],
            cell=observation["cell"],
            line=observation["line"],
        )
        line.append(f" [{target}]")
        if observation["snippet"] is not None:
            line.append(": ")
            line.append_text(_terminal_text(observation["snippet"]))
        if observation["evidence_ids"]:
            line.append(" (evidence: ")
            line.append_text(_terminal_text(", ".join(observation["evidence_ids"])))
            line.append(")")
        console.print(line)
    for label, field in (
        ("Skipped: ", "skipped_inputs"),
        ("Failed: ", "failed_inputs"),
    ):
        for coverage in dependency_result[field]:
            line = Text(label)
            line.append_text(_terminal_text(coverage["reason_code"]))
            target = _source_target(
                coverage["source_id"],
                coverage["member_id"],
            )
            line.append(f" [{target}]")
            console.print(line)

    for limit in report["limits"]["reached"]:
        _print_field(console, "Reached limit: ", limit)

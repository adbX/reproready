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


def _target(member_id: object) -> str:
    return "archive" if member_id is None else str(member_id)


def render_report(report: dict[str, object], console: Console) -> None:
    """Render the archive result without interpreting artifact-derived markup."""

    artifact = report["artifact"]
    archive_result = report["rule_results"][0]
    _print_field(console, "Artifact: ", artifact["display_name"])
    _print_field(console, "Kind: ", artifact["detected_kind"])
    _print_field(console, "archive.structure: ", archive_result["status"])

    observations = archive_result["observations"]
    if archive_result["status"] == "complete" and not observations:
        console.print(Text("no finding in the checks run"))
    for observation in observations:
        line = Text("Finding: ")
        line.append_text(_terminal_text(observation["condition_code"]))
        line.append(f" [{_target(observation['member_id'])}]")
        if observation["snippet"] is not None:
            line.append(": ")
            line.append_text(_terminal_text(observation["snippet"]))
        console.print(line)

    for limit in report["limits"]["reached"]:
        _print_field(console, "Reached limit: ", limit)
    for label, field in (
        ("Skipped: ", "skipped_inputs"),
        ("Failed: ", "failed_inputs"),
    ):
        for coverage in archive_result[field]:
            line = Text(label)
            line.append_text(_terminal_text(coverage["reason_code"]))
            line.append(f" [{_target(coverage['member_id'])}]")
            console.print(line)

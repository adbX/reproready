"""Collect literal absolute paths from supported Python call positions."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .checker_python import ParsedPythonSource

_CONSTRUCTORS = (
    "Path",
    "PurePath",
    "PosixPath",
    "PurePosixPath",
    "WindowsPath",
    "PureWindowsPath",
)
_ARGUMENTS: dict[str, tuple[tuple[int, str | None], ...]] = {
    "open": ((0, "file"),),
    "io.open": ((0, "file"),),
    "os.chdir": ((0, "path"),),
    "os.listdir": ((0, "path"),),
    "os.scandir": ((0, "path"),),
    "os.mkdir": ((0, "path"),),
    "os.remove": ((0, "path"),),
    "os.unlink": ((0, "path"),),
    "os.rmdir": ((0, "path"),),
    "os.stat": ((0, "path"),),
    "os.lstat": ((0, "path"),),
    "os.access": ((0, "path"),),
    "os.makedirs": ((0, "name"),),
    "os.rename": ((0, "src"), (1, "dst")),
    "os.replace": ((0, "src"), (1, "dst")),
    "os.walk": ((0, "top"),),
    "os.path.exists": ((0, "path"),),
    "os.path.isfile": ((0, "path"),),
    "os.path.isdir": ((0, "path"),),
    "os.path.getsize": ((0, "path"),),
    **{name: ((0, None),) for name in _CONSTRUCTORS},
    **{f"pathlib.{name}": ((0, None),) for name in _CONSTRUCTORS},
}


@dataclass(frozen=True, slots=True)
class AbsolutePathCandidate:
    """One reduced report observation plus transient deterministic sort fields."""

    observation: dict[str, object]
    decoded_literal: str
    occurrence: tuple[int, int]

    def sort_key(self) -> tuple[int, int, int, int, str, str, tuple[int, int]]:
        """Return the frozen ordering key without retaining parsed source state."""

        source_id = str(self.observation["source_id"])
        member_id = self.observation["member_id"]
        cell = self.observation["cell"]
        return (
            int(source_id.split(":", 1)[1]),
            -1 if member_id is None else int(str(member_id).split(":", 1)[1]),
            -1 if cell is None else int(cell),
            int(self.observation["line"]),
            str(self.observation["condition_code"]),
            self.decoded_literal,
            self.occurrence,
        )


def classify_absolute_path(value: str) -> str | None:
    """Classify frozen path syntax without consulting host path behavior."""

    if (
        len(value) >= 4
        and value[:2] == "\\\\"
        and value[2] in "?."
        and value[3] == "\\"
    ):
        return "windows_device_path"
    if len(value) >= 3 and value[:2] in {"//", "\\\\"} and value[2] not in "/\\":
        return "windows_unc_path"
    if (
        len(value) >= 3
        and ("A" <= value[0] <= "Z" or "a" <= value[0] <= "z")
        and value[1] == ":"
        and value[2] in "/\\"
    ):
        return "windows_drive_path"
    if (
        len(value) >= 2
        and value[0] in "/\\"
        and value[1] in "/\\"
        and value[0] != value[1]
    ):
        return None
    if value.startswith("/"):
        return "posix_absolute_path"
    if value == "~":
        return "tilde_path"
    if value.startswith("~") and any(
        index > 0 and character in "/\\" for index, character in enumerate(value)
    ):
        return "tilde_path"
    return None


def _callee_spelling(node: ast.expr) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _selected_arguments(
    call: ast.Call,
    selectors: tuple[tuple[int, str | None], ...],
) -> list[tuple[int, ast.expr]]:
    positions = {position for position, _ in selectors}
    keywords = {keyword for _, keyword in selectors if keyword is not None}
    selected: list[tuple[int, ast.expr]] = []
    occurrence = 0
    for position, argument in enumerate(call.args):
        if position in positions and not isinstance(argument, ast.Starred):
            selected.append((occurrence, argument))
        occurrence += 1
    for keyword in call.keywords:
        if keyword.arg in keywords:
            selected.append((occurrence, keyword.value))
        occurrence += 1
    return selected


def collect_absolute_path_observations(
    parsed: ParsedPythonSource,
) -> list[AbsolutePathCandidate]:
    """Reduce supported calls in one parsed source to bounded observations."""

    candidates: list[AbsolutePathCandidate] = []
    for call_index, node in enumerate(ast.walk(parsed.tree)):
        if not isinstance(node, ast.Call):
            continue
        spelling = _callee_spelling(node.func)
        selectors = None if spelling is None else _ARGUMENTS.get(spelling)
        if selectors is None:
            continue
        matches: list[tuple[int, str, str]] = []
        for argument_occurrence, argument in _selected_arguments(node, selectors):
            if (
                not isinstance(argument, ast.Constant)
                or type(argument.value) is not str
            ):
                continue
            condition = classify_absolute_path(argument.value)
            if condition is not None:
                matches.append((argument_occurrence, condition, argument.value))
        if not matches:
            continue
        location = parsed.location(node)
        syntax = str(location["syntax"])
        snippet = syntax[:240]
        for argument_occurrence, condition, decoded_literal in matches:
            observation: dict[str, object] = {
                "observation_id": "observation:9999",
                "rule_id": "python.absolute-path",
                "kind": "finding",
                "condition_code": condition,
                "member_id": location["member_id"],
                "source_id": location["source_id"],
                "line": location["line"],
                "cell": location["cell"],
                "snippet": snippet,
                "snippet_truncated": len(syntax) > 240,
                "evidence_ids": [],
            }
            candidates.append(
                AbsolutePathCandidate(
                    observation,
                    decoded_literal,
                    (call_index, argument_occurrence),
                )
            )
    candidates.sort(key=AbsolutePathCandidate.sort_key)
    return candidates

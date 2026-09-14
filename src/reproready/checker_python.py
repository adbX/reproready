"""Transient Python and notebook parsing for bounded checker intake."""

from __future__ import annotations

import ast
import json
import warnings
from dataclasses import dataclass


class NotebookParseError(ValueError):
    """A notebook document does not have the supported JSON structure."""


class NotebookCellSourceError(ValueError):
    """A notebook code cell does not contain supported source text."""


@dataclass(frozen=True, slots=True)
class ParsedPythonSource:
    """Bind one admitted Python AST to its exact source identity and text."""

    source_id: str
    member_id: str | None
    cell: int | None
    source: str
    tree: ast.Module

    def location(self, node: ast.AST) -> dict[str, str | int | None]:
        """Return the exact one-based location and syntax for a located node."""

        line = getattr(node, "lineno", None)
        if type(line) is not int or line <= 0:
            raise RuntimeError("a located AST node must have a positive one-based line")
        syntax = ast.get_source_segment(self.source, node)
        if syntax is None:
            raise RuntimeError("a located AST node must have recoverable source syntax")
        return {
            "source_id": self.source_id,
            "member_id": self.member_id,
            "cell": self.cell,
            "line": line,
            "syntax": syntax,
        }


def parse_python_source(
    source_id: str,
    member_id: str | None,
    cell: int | None,
    source: str,
) -> ParsedPythonSource:
    """Parse one Python source exactly once and retain its identity transiently."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(source)
    return ParsedPythonSource(source_id, member_id, cell, source, tree)


def parse_notebook_document(source: str) -> dict[str, object]:
    """Parse one notebook JSON document and enforce the supported outer shape."""

    try:
        document = json.loads(source)
    except json.JSONDecodeError as error:
        raise NotebookParseError from error
    if not isinstance(document, dict) or not isinstance(document.get("cells"), list):
        raise NotebookParseError
    return document


def notebook_cell_source(cell: dict[str, object]) -> str:
    """Return one code cell's validated source, joining list form exactly once."""

    source = cell.get("source")
    if isinstance(source, str):
        return source
    if isinstance(source, list):
        if any(not isinstance(item, str) for item in source):
            raise NotebookCellSourceError
        return "".join(source)
    raise NotebookCellSourceError


def has_unsupported_notebook_syntax(source: str) -> bool:
    """Return whether a line begins with notebook magic or shell syntax."""

    at_line_start = True
    for character in source:
        if character in "\r\n":
            at_line_start = True
        elif at_line_start and character.isspace():
            continue
        elif at_line_start:
            if character in "%!":
                return True
            at_line_start = False
    return False

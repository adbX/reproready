"""Typed public records for the static checker."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

_Record = Mapping[str, object]


class CheckInputError(Exception):
    """A top-level checker input rejected with a stable, sanitized reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CheckMember:
    """One inventory member from a completed checker document."""

    container_id: str
    member_id: str
    parent_member_id: str | None
    name: str
    duplicate_ordinal: int
    kind: str
    compressed_size: int | None
    expanded_size: int | None
    read_status: str
    integrity_status: str
    issues: tuple[_Record, ...]

    @classmethod
    def _from_record(cls, record: _Record) -> CheckMember:
        return cls(
            container_id=cast(str, record["container_id"]),
            member_id=cast(str, record["member_id"]),
            parent_member_id=cast(str | None, record["parent_member_id"]),
            name=cast(str, record["name"]),
            duplicate_ordinal=cast(int, record["duplicate_ordinal"]),
            kind=cast(str, record["kind"]),
            compressed_size=cast(int | None, record["compressed_size"]),
            expanded_size=cast(int | None, record["expanded_size"]),
            read_status=cast(str, record["read_status"]),
            integrity_status=cast(str, record["integrity_status"]),
            issues=tuple(cast(list[_Record], record["issues"])),
        )


@dataclass(frozen=True, slots=True)
class CheckReport:
    """One typed, schema-shaped result from :func:`reproready.check_path`."""

    schema_version: str
    tool_version: str
    ruleset_version: str
    artifact: _Record
    runtime: _Record
    limits: _Record
    inventory_status: str
    members: tuple[CheckMember, ...]
    inventory_issues: tuple[_Record, ...]
    source_index: tuple[_Record, ...]
    rule_results: tuple[_Record, ...]
    _document: dict[str, object] = field(repr=False, compare=False)

    @classmethod
    def _from_document(cls, document: dict[str, object]) -> CheckReport:
        inventory = cast(_Record, document["inventory"])
        member_records = cast(list[_Record], inventory["members"])
        return cls(
            schema_version=cast(str, document["schema_version"]),
            tool_version=cast(str, document["tool_version"]),
            ruleset_version=cast(str, document["ruleset_version"]),
            artifact=cast(_Record, document["artifact"]),
            runtime=cast(_Record, document["runtime"]),
            limits=cast(_Record, document["limits"]),
            inventory_status=cast(str, inventory["status"]),
            members=tuple(
                CheckMember._from_record(record) for record in member_records
            ),
            inventory_issues=tuple(cast(list[_Record], inventory["issues"])),
            source_index=tuple(cast(list[_Record], document["source_index"])),
            rule_results=tuple(cast(list[_Record], document["rule_results"])),
            _document=document,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the retained schema document as a borrowed, read-only dictionary."""

        return self._document

"""Typed public records for the static checker."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

_Record = Mapping[str, object]


class CheckInputError(Exception):
    """A top-level checker input rejected with a stable, sanitized reason.

    ``code`` is one of ``source_not_found``, ``source_not_accessible``,
    ``top_level_link``, ``not_regular_file``, or ``unsupported_system``.
    ``message`` never includes a host path or raw operating-system exception.

    Attributes:
        code: Stable machine-readable rejection reason.
        message: Sanitized explanation suitable for display.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CheckMember:
    """One inventory member from a completed checker document.

    Attributes:
        container_id: Stable identifier for the containing ZIP.
        member_id: Stable identifier for this inventory member.
        parent_member_id: Identifier of the parent member for nested content.
        name: Decoded member name retained in the report.
        duplicate_ordinal: One-based occurrence among entries with this name.
        kind: Recorded member kind.
        compressed_size: Compressed byte size when available.
        expanded_size: Expanded byte size when available.
        read_status: Recorded member read outcome.
        integrity_status: Recorded member integrity outcome.
        issues: Saved issue records for this member.
    """

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
    issues: tuple[Mapping[str, object], ...]

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
    """A typed view of one schema-shaped checker report.

    Attributes:
        schema_version: Version of the machine-readable report contract.
        tool_version: ReproReady version that created the report.
        ruleset_version: Version of the checker ruleset.
        artifact: Saved artifact identity and snapshot record.
        runtime: Saved operating-system, Python, and parser versions.
        limits: Effective and reached inspection limits.
        inventory_status: Overall saved inventory status.
        members: Inventory members in deterministic report order.
        inventory_issues: Saved inventory-level issue records.
        source_index: Saved source records in deterministic report order.
        rule_results: Ordered result record for every rule.
    """

    schema_version: str
    tool_version: str
    ruleset_version: str
    artifact: Mapping[str, object]
    runtime: Mapping[str, object]
    limits: Mapping[str, object]
    inventory_status: str
    members: tuple[CheckMember, ...]
    inventory_issues: tuple[Mapping[str, object], ...]
    source_index: tuple[Mapping[str, object], ...]
    rule_results: tuple[Mapping[str, object], ...]
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
        """Return the retained report dictionary without copying it.

        The checker does not reread or reinspect the input. The returned
        dictionary and its nested records are borrowed from this report and
        must be treated as read-only.
        """

        return self._document

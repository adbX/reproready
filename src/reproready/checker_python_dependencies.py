"""Reduce Python imports and direct declarations into bounded dependency evidence."""

from __future__ import annotations

import ast
import json
import re
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version

from packaging.requirements import InvalidRequirement, Requirement

from .checker_intake import FIXED_LIMITS
from .checker_python import ParsedPythonSource

PACKAGING_VERSION = version("packaging")
_MATCH_SEPARATOR = re.compile(r"[-_.]+")
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_ARCHIVE_SUFFIXES = (".whl", ".zip", ".tar", ".tar.gz", ".tgz")


@dataclass(frozen=True, slots=True)
class VirtualLocation:
    """A source identity in a host-independent virtual container."""

    container_id: str
    components: tuple[str, ...]
    member_id: str | None
    source_id: str


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    """One absolute import alias or relative import target from a parsed AST."""

    location: VirtualLocation
    cell: int | None
    line: int
    syntax: str
    snippet_truncated: bool
    name: str
    relative: bool
    occurrence: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class DeclarationCandidate:
    """One supported direct declaration and its transient parsed identity."""

    location: VirtualLocation
    line: int | None
    value: str
    snippet: str
    snippet_truncated: bool
    distribution_name: str
    match_key: str
    occurrence: int


@dataclass(frozen=True, slots=True)
class UnsupportedDependencyContent:
    """One source-local reason that dependency coverage is incomplete."""

    member_id: str | None
    source_id: str
    reason_code: str
    message: str


@dataclass(frozen=True, slots=True)
class DeclarationEvent:
    """One streaming declaration reduction result."""

    candidate: DeclarationCandidate | None = None
    unsupported: UnsupportedDependencyContent | None = None
    parser_examined: bool = False


def minimum_reduced_candidate_size(
    candidate: ImportCandidate | DeclarationCandidate,
) -> int:
    """Measure a conservative eventual evidence record for transient buffering."""

    if isinstance(candidate, ImportCandidate):
        kind = "local_module" if candidate.relative else "python_import"
        value = candidate.name
        line = candidate.line
        cell = candidate.cell
    else:
        kind = "dependency_declaration"
        value = candidate.value
        line = candidate.line
        cell = None
    return len(
        json.dumps(
            {
                "evidence_id": "evidence:0",
                "kind": kind,
                "member_id": candidate.location.member_id,
                "source_id": candidate.location.source_id,
                "line": line,
                "cell": cell,
                "value": value,
                "related_evidence_ids": [],
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


@dataclass(frozen=True, slots=True)
class VirtualFile:
    """One virtual regular or non-directory artifact entry used for root analysis."""

    container_id: str
    components: tuple[str, ...]
    name: str
    member_id: str | None
    source_id: str | None
    kind: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """One fully reduced neutral fact awaiting bounded report admission."""

    key: tuple[object, ...]
    kind: str
    member_id: str | None
    source_id: str | None
    line: int | None
    cell: int | None
    value: str
    occurrence: tuple[int, ...]
    related_keys: tuple[tuple[object, ...], ...] = ()
    match_group: tuple[str, tuple[str, ...], str] | None = None
    match_role: str | None = None

    @property
    def sort_key(self) -> tuple[object, ...]:
        return (
            _record_ordinal(self.source_id),
            _record_ordinal(self.member_id),
            -1 if self.cell is None else self.cell,
            -1 if self.line is None else self.line,
            self.kind,
            self.value,
            self.occurrence,
        )

    def minimum_record_size(self) -> int:
        """Return a conservative encoded size with the shortest valid ID."""

        return len(
            json.dumps(
                {
                    "evidence_id": "evidence:0",
                    "kind": self.kind,
                    "member_id": self.member_id,
                    "source_id": self.source_id,
                    "line": self.line,
                    "cell": self.cell,
                    "value": self.value,
                    "related_evidence_ids": [],
                },
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    """One unmatched occurrence awaiting evidence-aware observation admission."""

    condition_code: str
    evidence_key: tuple[object, ...]
    member_id: str | None
    source_id: str
    line: int | None
    cell: int | None
    snippet: str
    snippet_truncated: bool
    occurrence: tuple[int, ...]

    @property
    def sort_key(self) -> tuple[object, ...]:
        return (
            _record_ordinal(self.source_id),
            _record_ordinal(self.member_id),
            -1 if self.cell is None else self.cell,
            -1 if self.line is None else self.line,
            self.condition_code,
            self.snippet,
            self.occurrence,
        )


@dataclass(frozen=True, slots=True)
class DependencyAnalysis:
    """Deterministic dependency facts, reviews, and analysis-time coverage."""

    evidence: tuple[EvidenceCandidate, ...]
    reviews: tuple[ReviewCandidate, ...]
    unsupported: tuple[UnsupportedDependencyContent, ...]


def split_virtual_name(name: str) -> tuple[str, ...]:
    """Split a decoded member name without normalizing any component."""

    return tuple(re.split(r"[/\\]", name))


def normalize_match_key(name: str) -> str:
    """Apply the frozen lexical dependency-name normalization."""

    return _MATCH_SEPARATOR.sub("-", name.lower())


def unsupported_dependency_form(name: str) -> bool:
    """Return whether a member basename is an explicitly unsupported form."""

    basename = split_virtual_name(name)[-1]
    folded = basename.casefold()
    return (
        folded == "setup.py"
        or folded == "pipfile"
        or folded.endswith(".lock")
        or (folded.startswith("requirements") and folded.endswith(".in"))
        or (folded.startswith("constraints") and folded.endswith((".txt", ".in")))
        or (
            folded.startswith(("environment", "conda"))
            and folded.endswith((".yml", ".yaml"))
        )
        or folded == "dockerfile"
        or folded.startswith("dockerfile.")
    )


def _bounded_snippet(value: str) -> tuple[str, bool]:
    limit = FIXED_LIMITS["max_snippet_codepoints"]
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _unsupported(
    location: VirtualLocation,
    reason_code: str,
    message: str,
) -> UnsupportedDependencyContent:
    return UnsupportedDependencyContent(
        location.member_id,
        location.source_id,
        reason_code,
        message,
    )


def collect_import_candidates(
    parsed: ParsedPythonSource,
    location: VirtualLocation,
) -> Iterator[ImportCandidate | UnsupportedDependencyContent]:
    """Reduce every syntactic import in one already parsed Python source."""

    node_occurrence = 0
    for node in ast.walk(parsed.tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        located = parsed.location(node)
        syntax, truncated = _bounded_snippet(str(located["syntax"]))
        line = int(located["line"])
        column = int(getattr(node, "col_offset", 0))
        reduced: list[tuple[str, bool]] = []
        if isinstance(node, ast.Import):
            reduced.extend((alias.name.split(".", 1)[0], False) for alias in node.names)
        elif node.level == 0 and node.module:
            reduced.append((node.module.split(".", 1)[0], False))
        elif node.level > 0 and node.module:
            reduced.append(("." * node.level + node.module, True))
        elif node.level > 0:
            reduced.extend(
                ("." * node.level + alias.name, True) for alias in node.names
            )
        for alias_occurrence, (name, relative) in enumerate(reduced):
            if len(name) > 4096:
                yield _unsupported(
                    location,
                    "dependency_evidence_value_limit",
                    "An import name exceeded the fixed evidence-value bound.",
                )
                continue
            yield ImportCandidate(
                location,
                parsed.cell,
                line,
                syntax,
                truncated,
                name,
                relative,
                (node_occurrence, column, alias_occurrence),
            )
        node_occurrence += 1


def _comment_free_requirement(line: str) -> str:
    for index, character in enumerate(line):
        if character == "#" and index > 0 and line[index - 1] in " \t\f\v":
            return line[:index].rstrip(" \t\f\v")
    return line


def _obvious_unsupported_requirement(value: str) -> bool:
    stripped = value.strip()
    folded = stripped.casefold()
    return (
        stripped.startswith(("-", ".", "/", "~", "\\"))
        or stripped.rstrip().endswith("\\")
        or _WINDOWS_PATH.match(stripped) is not None
        or folded.startswith(
            (
                "http://",
                "https://",
                "file:",
                "git+",
                "hg+",
                "svn+",
                "bzr+",
            )
        )
        or folded.endswith(_ARCHIVE_SUFFIXES)
    )


def _declaration_event(
    location: VirtualLocation,
    *,
    line: int | None,
    value: str,
    snippet: str,
    occurrence: int,
) -> DeclarationEvent:
    if len(value) > 4096:
        return DeclarationEvent(
            unsupported=_unsupported(
                location,
                "unsupported_dependency_syntax",
                "A dependency declaration exceeded the fixed evidence-value bound.",
            )
        )
    if _obvious_unsupported_requirement(value):
        return DeclarationEvent(
            unsupported=_unsupported(
                location,
                "unsupported_dependency_syntax",
                "A direct dependency declaration used unsupported syntax.",
            )
        )
    try:
        requirement = Requirement(value)
    except InvalidRequirement:
        return DeclarationEvent(
            unsupported=_unsupported(
                location,
                "unsupported_dependency_syntax",
                "A direct dependency declaration used unsupported syntax.",
            ),
            parser_examined=True,
        )
    if requirement.url is not None or requirement.marker is not None:
        return DeclarationEvent(
            unsupported=_unsupported(
                location,
                "unsupported_dependency_syntax",
                "A direct dependency declaration used unsupported syntax.",
            ),
            parser_examined=True,
        )
    bounded, truncated = _bounded_snippet(snippet)
    return DeclarationEvent(
        candidate=DeclarationCandidate(
            location,
            line,
            value,
            bounded,
            truncated,
            requirement.name,
            normalize_match_key(requirement.name),
            occurrence,
        ),
        parser_examined=True,
    )


def reduce_requirements_source(
    location: VirtualLocation,
    text: str,
) -> Iterator[DeclarationEvent]:
    """Stream supported physical requirements lines and unsupported flags."""

    for line_number, physical_line in enumerate(re.split(r"\r\n|\n|\r", text), start=1):
        stripped = physical_line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        value = _comment_free_requirement(physical_line)
        yield _declaration_event(
            location,
            line=line_number,
            value=value,
            snippet=physical_line,
            occurrence=line_number,
        )


def validate_pyproject_document(
    document: Mapping[str, object],
) -> tuple[dict[str, object], list[str]]:
    """Return the validated PEP 621 project table and dependency array."""

    project = document.get("project", {})
    if not isinstance(project, dict):
        raise TypeError("project must be a table")
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or any(
        not isinstance(item, str) for item in dependencies
    ):
        raise TypeError("project.dependencies must be an array of strings")
    return project, dependencies


def reduce_pyproject_document(
    location: VirtualLocation,
    document: Mapping[str, object],
) -> Iterator[DeclarationEvent]:
    """Stream supported PEP 621 declarations from one parsed TOML document."""

    project, dependencies = validate_pyproject_document(document)

    tool = document.get("tool")
    unsupported_tables = (
        "optional-dependencies" in project
        or "dependency-groups" in document
        or (isinstance(tool, dict) and ("poetry" in tool or "pdm" in tool))
    )
    if unsupported_tables:
        yield DeclarationEvent(
            unsupported=_unsupported(
                location,
                "unsupported_dependency_table",
                "The pyproject contains dependency tables outside PEP 621 project.dependencies.",
            )
        )
    for occurrence, item in enumerate(dependencies):
        assert isinstance(item, str)
        yield _declaration_event(
            location,
            line=None,
            value=item,
            snippet=item,
            occurrence=occurrence,
        )


def _record_ordinal(record_id: str | None) -> int:
    if record_id is None:
        return -1
    return int(record_id.split(":", 1)[1])


def _is_prefix(prefix: tuple[str, ...], path: tuple[str, ...]) -> bool:
    return len(prefix) <= len(path) and path[: len(prefix)] == prefix


def _fallback_roots(files: Sequence[VirtualFile]) -> dict[str, tuple[str, ...]]:
    by_container: dict[str, list[VirtualFile]] = {}
    for file in files:
        if file.kind != "directory":
            by_container.setdefault(file.container_id, []).append(file)
    roots: dict[str, tuple[str, ...]] = {}
    for container_id, entries in by_container.items():
        first = (
            entries[0].components[0]
            if entries and len(entries[0].components) >= 2
            else None
        )
        if first is not None and all(
            len(entry.components) >= 2 and entry.components[0] == first
            for entry in entries
        ):
            roots[container_id] = (first,)
        else:
            roots[container_id] = ()
    return roots


def _assigned_root(
    location: VirtualLocation,
    dependency_roots: Mapping[str, set[tuple[str, ...]]],
    fallback_roots: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    ancestors = [
        root
        for root in dependency_roots.get(location.container_id, set())
        if _is_prefix(root, location.components[:-1])
    ]
    if ancestors:
        return max(ancestors, key=len)
    return fallback_roots.get(location.container_id, ())


def _crosses_nested_root(
    assigned_root: tuple[str, ...],
    target: tuple[str, ...],
    roots: Iterable[tuple[str, ...]],
) -> bool:
    return any(
        root != assigned_root
        and len(root) > len(assigned_root)
        and _is_prefix(assigned_root, root)
        and _is_prefix(root, target)
        for root in roots
    )


def analyze_dependencies(
    imports: Sequence[ImportCandidate],
    declarations: Sequence[DeclarationCandidate],
    unsupported: Sequence[UnsupportedDependencyContent],
    files: Sequence[VirtualFile],
    source_index: Sequence[Mapping[str, object]],
    source_locations: Mapping[str, VirtualLocation],
) -> DependencyAnalysis:
    """Resolve roots, matches, runtime classifications, facts, and reviews."""

    dependency_roots: dict[str, set[tuple[str, ...]]] = {}
    for source in source_index:
        if source["form"] not in {"requirements", "pyproject"}:
            continue
        location = source_locations.get(str(source["source_id"]))
        if location is not None:
            dependency_roots.setdefault(location.container_id, set()).add(
                location.components[:-1]
            )
    fallback_roots = _fallback_roots(files)

    import_roots = {
        candidate: _assigned_root(candidate.location, dependency_roots, fallback_roots)
        for candidate in imports
    }
    declaration_roots = {
        candidate: candidate.location.components[:-1] for candidate in declarations
    }
    import_names: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    declaration_names: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for candidate, root in import_roots.items():
        if not candidate.relative:
            import_names.setdefault((candidate.location.container_id, root), set()).add(
                normalize_match_key(candidate.name)
            )
    for candidate, root in declaration_roots.items():
        declaration_names.setdefault(
            (candidate.location.container_id, root), set()
        ).add(candidate.match_key)

    files_by_path: dict[tuple[str, tuple[str, ...]], list[VirtualFile]] = {}
    for file in files:
        if file.kind == "file":
            files_by_path.setdefault((file.container_id, file.components), []).append(
                file
            )

    evidence: list[EvidenceCandidate] = []
    reviews: list[ReviewCandidate] = []
    analysis_coverage = list(unsupported)
    for candidate, root in import_roots.items():
        base_key = (
            "import",
            candidate.location.source_id,
            candidate.cell,
            *candidate.occurrence,
        )
        if candidate.relative:
            evidence.append(
                EvidenceCandidate(
                    base_key,
                    "local_module",
                    candidate.location.member_id,
                    candidate.location.source_id,
                    candidate.line,
                    candidate.cell,
                    candidate.name,
                    (1, *candidate.occurrence),
                )
            )
            continue

        classification: list[EvidenceCandidate] = []
        has_classification = candidate.name in sys.stdlib_module_names
        if candidate.name in sys.stdlib_module_names:
            key = (*base_key, "stdlib")
            classification.append(
                EvidenceCandidate(
                    key,
                    "standard_library",
                    candidate.location.member_id,
                    candidate.location.source_id,
                    candidate.line,
                    candidate.cell,
                    candidate.name,
                    (2, *candidate.occurrence),
                    (base_key,),
                )
            )
            has_classification = True

        layouts = (
            (candidate.name + ".py",),
            (candidate.name, "__init__.py"),
            ("src", candidate.name + ".py"),
            ("src", candidate.name, "__init__.py"),
        )
        local_files: list[VirtualFile] = []
        for layout in layouts:
            target = (*root, *layout)
            if _crosses_nested_root(
                root,
                target,
                dependency_roots.get(candidate.location.container_id, set()),
            ):
                continue
            local_files.extend(
                files_by_path.get((candidate.location.container_id, target), ())
            )
        if local_files:
            has_classification = True
        for local_file in local_files:
            value = local_file.name
            if len(value) > 4096:
                analysis_coverage.append(
                    _unsupported(
                        candidate.location,
                        "dependency_evidence_value_limit",
                        "A local-module member name exceeded the fixed evidence-value bound.",
                    )
                )
                continue
            key = (*base_key, "local", local_file.ordinal)
            classification.append(
                EvidenceCandidate(
                    key,
                    "local_module",
                    local_file.member_id,
                    local_file.source_id,
                    None,
                    None,
                    value,
                    (3, *candidate.occurrence, local_file.ordinal),
                    (base_key,),
                )
            )

        evidence.append(
            EvidenceCandidate(
                base_key,
                "python_import",
                candidate.location.member_id,
                candidate.location.source_id,
                candidate.line,
                candidate.cell,
                candidate.name,
                (0, *candidate.occurrence),
                tuple(item.key for item in classification),
                (
                    candidate.location.container_id,
                    root,
                    normalize_match_key(candidate.name),
                ),
                "import",
            )
        )
        evidence.extend(classification)
        group = (candidate.location.container_id, root)
        matched = normalize_match_key(candidate.name) in declaration_names.get(
            group, set()
        )
        if not matched and not has_classification:
            reviews.append(
                ReviewCandidate(
                    "import_without_exact_declaration",
                    base_key,
                    candidate.location.member_id,
                    candidate.location.source_id,
                    candidate.line,
                    candidate.cell,
                    candidate.syntax,
                    candidate.snippet_truncated,
                    (0, *candidate.occurrence),
                )
            )

    for candidate, root in declaration_roots.items():
        key = (
            "declaration",
            candidate.location.source_id,
            candidate.occurrence,
        )
        evidence.append(
            EvidenceCandidate(
                key,
                "dependency_declaration",
                candidate.location.member_id,
                candidate.location.source_id,
                candidate.line,
                None,
                candidate.value,
                (4, candidate.occurrence),
                (),
                (candidate.location.container_id, root, candidate.match_key),
                "declaration",
            )
        )
        group = (candidate.location.container_id, root)
        if candidate.match_key not in import_names.get(group, set()):
            reviews.append(
                ReviewCandidate(
                    "declaration_without_exact_import",
                    key,
                    candidate.location.member_id,
                    candidate.location.source_id,
                    candidate.line,
                    None,
                    candidate.snippet,
                    candidate.snippet_truncated,
                    (4, candidate.occurrence),
                )
            )

    evidence.sort(key=lambda item: item.sort_key)
    reviews.sort(key=lambda item: item.sort_key)
    unique_coverage: dict[
        tuple[str | None, str, str], UnsupportedDependencyContent
    ] = {}
    for item in analysis_coverage:
        unique_coverage.setdefault(
            (item.member_id, item.source_id, item.reason_code), item
        )
    return DependencyAnalysis(
        tuple(evidence),
        tuple(reviews),
        tuple(unique_coverage.values()),
    )

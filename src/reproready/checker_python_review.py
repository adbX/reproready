"""Reduce retained Python review conditions from the bounded source pass."""

from __future__ import annotations

import ast
import io
import re
import shlex
import tokenize
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from .checker_intake import FIXED_LIMITS
from .checker_python import ParsedPythonSource

RETAINED_RULE_IDS = (
    "python.sys-path-three-dot",
    "python.download-comment-http-url",
    "python.open-bundled-archive-member",
    "python.pandas-csv-inventory-absence",
    "python.notebook-pip-install",
    "python.gdown-anonymized-value",
    "python.entry-point-input",
    "python.gfile-bucket-authority",
)

_COMMENT_RULE = "python.download-comment-http-url"
_OPEN_RULE = "python.open-bundled-archive-member"
_PANDAS_RULE = "python.pandas-csv-inventory-absence"
_PIP_RULE = "python.notebook-pip-install"
_GDOWN_RULE = "python.gdown-anonymized-value"
_ENTRY_RULE = "python.entry-point-input"
_SYS_PATH_RULE = "python.sys-path-three-dot"
_GFILE_RULE = "python.gfile-bucket-authority"
_FILE_AST_RULES = (_SYS_PATH_RULE, _PANDAS_RULE, _GFILE_RULE)
_SHARED_AST_RULES = (_OPEN_RULE, _GDOWN_RULE, _ENTRY_RULE)
_NOTEBOOK_RULES = (_COMMENT_RULE, _OPEN_RULE, _PIP_RULE, _GDOWN_RULE, _ENTRY_RULE)
_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_DOWNLOAD_RE = re.compile(r"\bdownload\b", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}"
_MUTATING_MAPPING_METHODS = {
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
}


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    """One supporting location for a retained review observation."""

    key: tuple[object, ...]
    kind: str
    member_id: str | None
    source_id: str | None
    line: int | None
    cell: int | None
    value: str


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    """One bounded retained-rule candidate awaiting report admission."""

    rule_id: str
    condition_code: str
    member_id: str | None
    source_id: str
    line: int
    cell: int | None
    snippet: str
    snippet_truncated: bool
    occurrence: int
    path: tuple[str, ...] | None = None
    evidence: ReviewEvidence | None = None

    def sort_key(self) -> tuple[int, int, int, int, str, str, int]:
        return (
            _record_ordinal(self.source_id),
            _record_ordinal(self.member_id),
            -1 if self.cell is None else self.cell,
            self.line,
            self.condition_code,
            self.snippet,
            self.occurrence,
        )


@dataclass(frozen=True, slots=True)
class ReviewGap:
    """One rule-specific source-inspection gap."""

    member_id: str | None
    source_id: str | None
    reason_code: str
    message: str
    failed: bool

    def sort_key(self) -> tuple[int, int, str, str]:
        return (
            _record_ordinal(self.source_id),
            _record_ordinal(self.member_id),
            self.reason_code,
            self.message,
        )


@dataclass(frozen=True, slots=True)
class _Binding:
    kind: str
    depth: int
    value: object = None
    evidence: ReviewEvidence | None = None


@dataclass(slots=True)
class _NotebookState:
    bindings: dict[str, _Binding]


@dataclass(slots=True)
class _Scope:
    bindings: dict[str, _Binding]
    lexical_names: set[str]
    depth: int
    is_module: bool


def _record_ordinal(record_id: str | None) -> int:
    if record_id is None:
        return -1
    return int(record_id.split(":", 1)[1])


def _bounded_snippet(value: str) -> tuple[str, bool]:
    limit = FIXED_LIMITS["max_snippet_codepoints"]
    return value[:limit], len(value) > limit


def normalize_relative_posix(value: str) -> tuple[str, ...] | None:
    """Normalize one supported relative POSIX path without host queries."""

    if (
        not value
        or value.startswith(("/", "~"))
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
    ):
        return None
    if value.endswith("/"):
        return None
    raw_parts = value.split("/")
    if raw_parts[-1] in {"", "."}:
        return None
    parts = tuple(part for part in raw_parts if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        return None
    return parts


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    return None


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return set().union(*(_target_names(item) for item in node.elts))
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    return set()


class _LexicalNames(ast.NodeVisitor):
    """Collect bindings in one lexical scope without entering child scopes."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.names.add(alias.asname or alias.name)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if isinstance(node.name, str):
            self.names.add(node.name)
        self.generic_visit(node)


def _lexical_names(
    body: Sequence[ast.stmt], arguments: ast.arguments | None
) -> set[str]:
    visitor = _LexicalNames()
    for statement in body:
        visitor.visit(statement)
    if arguments is not None:
        positional = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        visitor.names.update(argument.arg for argument in positional)
        if arguments.vararg is not None:
            visitor.names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            visitor.names.add(arguments.kwarg.arg)
    return visitor.names


class ReviewCollector:
    """Own bounded retained-rule facts and notebook-local carry state."""

    def __init__(self) -> None:
        self.candidates: dict[str, list[ReviewCandidate]] = {
            rule_id: [] for rule_id in RETAINED_RULE_IDS
        }
        self.omitted: dict[str, ReviewCandidate] = {}
        self.lexical_examined: set[str] = set()
        self.shell_examined: set[str] = set()
        self.extra_gaps: dict[str, list[ReviewGap]] = {
            rule_id: [] for rule_id in RETAINED_RULE_IDS
        }
        self._notebooks: dict[str | None, _NotebookState] = {}
        self._occurrence = 0

    def begin_notebook(self, member_id: str | None) -> None:
        self._notebooks[member_id] = _NotebookState({})

    def end_notebook(self, member_id: str | None) -> None:
        self._notebooks.pop(member_id, None)

    def invalidate_notebook(self, member_id: str | None) -> None:
        state = self._notebooks.get(member_id)
        if state is not None:
            state.bindings = {
                "input": _Binding("unknown", 0),
                "open": _Binding("unknown", 0),
            }

    def inspect_python_lexical(
        self,
        source_id: str,
        member_id: str | None,
        cell: int | None,
        source: str,
    ) -> None:
        """Tokenize one decoded Python context independently from its AST."""

        self.lexical_examined.add(source_id)
        physical_lines = source.splitlines()
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                if token.type != tokenize.COMMENT:
                    continue
                comment = token.string
                if _DOWNLOAD_RE.search(comment) is None:
                    continue
                if not _comment_has_http_url(comment):
                    continue
                snippet, truncated = _bounded_snippet(
                    physical_lines[token.start[0] - 1]
                )
                self._add(
                    ReviewCandidate(
                        _COMMENT_RULE,
                        "download_comment_with_http_url",
                        member_id,
                        source_id,
                        token.start[0],
                        cell,
                        snippet,
                        truncated,
                        self._next_occurrence(),
                    )
                )
        except (tokenize.TokenError, IndentationError, SyntaxError):
            self.extra_gaps[_COMMENT_RULE].append(
                ReviewGap(
                    member_id,
                    source_id,
                    "python_tokenize_error",
                    "This Python context could not be tokenized completely.",
                    True,
                )
            )

    def inspect_notebook_shell(
        self,
        source_id: str,
        member_id: str | None,
        cell: int,
        source: str,
    ) -> bool:
        """Reduce exact notebook shell lines before Python syntax rejection."""

        self.shell_examined.add(source_id)
        attempted = False
        for line_number, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if not stripped.startswith("!"):
                continue
            attempted = True
            try:
                tokens = shlex.split(stripped[1:], comments=False, posix=True)
            except ValueError:
                self.extra_gaps[_PIP_RULE].append(
                    ReviewGap(
                        member_id,
                        source_id,
                        "notebook_shell_parse_error",
                        "This notebook shell line could not be parsed completely.",
                        True,
                    )
                )
                continue
            if tokens[:2] != ["pip", "install"]:
                continue
            snippet, truncated = _bounded_snippet(line)
            self._add(
                ReviewCandidate(
                    _PIP_RULE,
                    "notebook_pip_install",
                    member_id,
                    source_id,
                    line_number,
                    cell,
                    snippet,
                    truncated,
                    self._next_occurrence(),
                )
            )
        return attempted

    def inspect_parsed(self, parsed: ParsedPythonSource) -> None:
        """Visit one already parsed source once for every AST-based retained rule."""

        state = None if parsed.cell is None else self._notebooks.get(parsed.member_id)
        seed = {} if state is None else dict(state.bindings)
        visitor = _RetainedVisitor(self, parsed, seed)
        visitor.visit(parsed.tree)
        if state is not None:
            state.bindings = visitor.notebook_bindings()

    def notebook_document_gap(
        self,
        member_id: str | None,
        source_id: str,
        reason_code: str,
    ) -> None:
        for rule_id in _NOTEBOOK_RULES:
            self.extra_gaps[rule_id].append(
                ReviewGap(
                    member_id,
                    source_id,
                    reason_code,
                    "This notebook document could not be inspected for this rule.",
                    True,
                )
            )

    def _next_occurrence(self) -> int:
        occurrence = self._occurrence
        self._occurrence += 1
        return occurrence

    def _add(self, candidate: ReviewCandidate) -> None:
        collection = self.candidates[candidate.rule_id]
        if len(collection) >= FIXED_LIMITS["max_observations"] - 1:
            self.omitted.setdefault(candidate.rule_id, candidate)
            return
        collection.append(candidate)

    def resolved_candidates(
        self,
        rule_id: str,
        members: Sequence[Mapping[str, object]],
        inventory_status: str,
    ) -> list[ReviewCandidate]:
        candidates = self.candidates[rule_id]
        if rule_id == _OPEN_RULE:
            if inventory_status != "complete":
                return []
            candidates = _resolve_open_candidates(candidates, members)
        elif rule_id == _PANDAS_RULE:
            if inventory_status != "complete":
                return []
            candidates = _resolve_pandas_candidates(candidates, members)
        return sorted(candidates, key=ReviewCandidate.sort_key)


def _comment_has_http_url(comment: str) -> bool:
    for match in _URL_RE.finditer(comment):
        candidate = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
        parsed = urlsplit(candidate)
        if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
            return True
    return False


class _EntryInputFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "input":
            self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


class _RetainedVisitor(ast.NodeVisitor):
    """Collect every AST-based retained selector in one traversal."""

    def __init__(
        self,
        collector: ReviewCollector,
        parsed: ParsedPythonSource,
        seed_bindings: dict[str, _Binding],
    ) -> None:
        self.collector = collector
        self.parsed = parsed
        self.seed_bindings = seed_bindings
        self.scopes: list[_Scope] = []

    @property
    def scope(self) -> _Scope:
        return self.scopes[-1]

    def notebook_bindings(self) -> dict[str, _Binding]:
        if self.parsed.cell is None or not self.scopes:
            return {}
        return dict(self.scopes[0].bindings)

    def visit_Module(self, node: ast.Module) -> None:
        self.scopes.append(
            _Scope(
                dict(self.seed_bindings),
                _lexical_names(node.body, None),
                0,
                True,
            )
        )
        for statement in node.body:
            self.visit(statement)

    def _enter_function(
        self,
        body: Sequence[ast.stmt],
        arguments: ast.arguments | None,
    ) -> None:
        visible = dict(self.scope.bindings)
        self.scopes.append(
            _Scope(
                visible,
                _lexical_names(body, arguments),
                self.scope.depth + 1,
                False,
            )
        )
        for statement in body:
            self.visit(statement)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        self._set_unknown(node.name)
        self._enter_function(node.body, node.args)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._set_unknown(node.name)
        self._enter_function(node.body, None)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.scopes.append(
            _Scope(
                dict(self.scope.bindings),
                _lexical_names([], node.args),
                self.scope.depth + 1,
                False,
            )
        )
        self.visit(node.body)
        self.scopes.pop()

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if self.scope.is_module and _canonical_entry_guard(node.test):
            self._collect_entry_inputs(node.body)
        changed = self._visit_branch(node.body)
        other_changed = self._visit_branch(node.orelse)
        self._apply_branch_changes(changed | other_changed)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        changed = self._visit_branch(node.body, targets=[node.target])
        other_changed = self._visit_branch(node.orelse)
        self._apply_branch_changes(changed | other_changed)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        changed = self._visit_branch(node.body)
        other_changed = self._visit_branch(node.orelse)
        self._apply_branch_changes(changed | other_changed)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
        targets = [item.optional_vars for item in node.items if item.optional_vars]
        changed = self._visit_branch(node.body, targets=targets)
        self._apply_branch_changes(changed)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def visit_Try(self, node: ast.Try) -> None:
        changes: set[str] = set()
        for body, targets in [
            (node.body, []),
            *[
                (
                    handler.body,
                    (
                        [ast.Name(id=handler.name, ctx=ast.Store())]
                        if isinstance(handler.name, str)
                        else []
                    ),
                )
                for handler in node.handlers
            ],
            (node.orelse, []),
            (node.finalbody, []),
        ]:
            changes.update(self._visit_branch(body, targets=targets))
        self._apply_branch_changes(changes)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self.visit_Try(node)

    def _visit_branch(
        self,
        body: Sequence[ast.stmt],
        *,
        targets: Sequence[ast.AST] = (),
    ) -> set[str]:
        bindings = self.scope.bindings
        self.scope.bindings = dict(bindings)
        for target in targets:
            for name in _target_names(target):
                self._set_unknown(name)
        for statement in body:
            self.visit(statement)
        branch_bindings = self.scope.bindings
        changed = {
            name
            for name in bindings.keys() | branch_bindings.keys()
            if bindings.get(name) != branch_bindings.get(name)
        }
        self.scope.bindings = bindings
        return changed

    def _apply_branch_changes(self, changed: Iterable[str]) -> None:
        for name in changed:
            self._set_unknown(name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            kind = "unknown"
            if alias.name == "pandas":
                kind = "pandas"
            elif alias.name == "gdown":
                kind = "gdown"
            elif alias.name == "tensorflow":
                kind = "tensorflow"
            elif alias.name == "tensorflow.io.gfile":
                kind = "tensorflow_gfile" if alias.asname else "tensorflow"
            elif alias.name == "os":
                kind = "os"
            elif alias.name == "os.path":
                kind = "os_path" if alias.asname else "os"
            self.scope.bindings[bound] = _Binding(kind, self.scope.depth)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level != 0:
            for alias in node.names:
                if alias.name != "*":
                    self._set_unknown(alias.asname or alias.name)
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name
            kind = "unknown"
            if node.module == "sys" and alias.name == "path":
                kind = "sys_path"
            elif node.module == "tensorflow.io.gfile" and alias.name == "GFile":
                kind = "gfile"
            elif node.module == "tensorflow.io" and alias.name == "gfile":
                kind = "tensorflow_gfile"
            elif node.module == "tensorflow" and alias.name == "io":
                kind = "tensorflow_io"
            elif node.module == "os" and alias.name == "path":
                kind = "os_path"
            elif node.module == "os.path" and alias.name == "join":
                kind = "os_join"
            self.scope.bindings[bound] = _Binding(kind, self.scope.depth)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            self._assign_name(node.targets[0].id, node.value)
            return
        for target in node.targets:
            self._invalidate_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        if isinstance(node.target, ast.Name) and node.value is not None:
            self._assign_name(node.target.id, node.value)
        else:
            self._invalidate_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._invalidate_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._invalidate_target(node.target)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._invalidate_target(target)

    def visit_Call(self, node: ast.Call) -> None:
        if self.parsed.cell is None:
            self._collect_sys_path(node)
            self._collect_pandas(node)
            self._collect_gfile(node)
        self._collect_open(node)
        self._collect_gdown(node)
        self.generic_visit(node)
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.attr in _MUTATING_MAPPING_METHODS
        ):
            binding = self._lookup(node.func.value.id)
            if binding is not None and binding.kind == "anon_mapping":
                self._set_unknown(node.func.value.id)

    def _assign_name(self, name: str, value: ast.AST) -> None:
        if name in self.scope.bindings:
            self._set_unknown(name)
            return
        literal = _literal_string(value)
        normalized = None if literal is None else normalize_relative_posix(literal)
        if normalized is not None:
            self.scope.bindings[name] = _Binding(
                "literal_path", self.scope.depth, normalized
            )
            return
        evidence = self._anonymized_mapping(value)
        if evidence is not None:
            self.scope.bindings[name] = _Binding(
                "anon_mapping", self.scope.depth, evidence=evidence
            )
            return
        evidence = self._anon_expression(value, direct_only=True)
        if evidence is not None:
            self.scope.bindings[name] = _Binding(
                "anon_flow", self.scope.depth, evidence=evidence
            )
            return
        self._set_unknown(name)

    def _anonymized_mapping(self, value: ast.AST) -> ReviewEvidence | None:
        if not isinstance(value, ast.Dict) or not value.keys:
            return None
        if any(key is None for key in value.keys):
            return None
        if any(_literal_string(item) != "ANONYMIZED" for item in value.values):
            return None
        first = value.values[0]
        location = self.parsed.location(first)
        return ReviewEvidence(
            (
                "anonymized_mapping_value",
                self.parsed.source_id,
                location["line"],
                self.collector._next_occurrence(),
            ),
            "anonymized_mapping_value",
            self.parsed.member_id,
            self.parsed.source_id,
            int(location["line"]),
            self.parsed.cell,
            "ANONYMIZED",
        )

    def _anon_expression(
        self, node: ast.AST, *, direct_only: bool = False
    ) -> ReviewEvidence | None:
        evidence: dict[tuple[object, ...], ReviewEvidence] = {}

        def inspect(current: ast.AST) -> bool:
            if isinstance(current, ast.Subscript) and isinstance(
                current.value, ast.Name
            ):
                binding = self._lookup(current.value.id)
                if (
                    binding is not None
                    and binding.kind == "anon_mapping"
                    and binding.depth == 0
                    and binding.evidence is not None
                ):
                    evidence[binding.evidence.key] = binding.evidence
                    return True
                return False
            if isinstance(current, ast.JoinedStr):
                return any(
                    inspect(item.value)
                    for item in current.values
                    if isinstance(item, ast.FormattedValue)
                )
            if isinstance(current, ast.BinOp) and isinstance(current.op, ast.Add):
                left = inspect(current.left)
                right = inspect(current.right)
                return left or right
            if not direct_only and isinstance(current, ast.Name):
                binding = self._lookup(current.id)
                if (
                    binding is not None
                    and binding.kind == "anon_flow"
                    and binding.evidence
                ):
                    evidence[binding.evidence.key] = binding.evidence
                    return True
            return False

        if not inspect(node) or len(evidence) != 1:
            return None
        return next(iter(evidence.values()))

    def _invalidate_target(self, target: ast.AST) -> None:
        for name in _target_names(target):
            self._set_unknown(name)
        if isinstance(target, ast.Subscript):
            root = target.value
            while isinstance(root, ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name):
                self._set_unknown(root.id)

    def _set_unknown(self, name: str) -> None:
        self.scope.bindings[name] = _Binding("unknown", self.scope.depth)

    def _lookup(self, name: str) -> _Binding | None:
        return self.scope.bindings.get(name)

    def _builtin_available(self, name: str) -> bool:
        if name in self.scope.lexical_names:
            return False
        binding = self._lookup(name)
        return binding is None

    def _add_ast_candidate(
        self,
        rule_id: str,
        condition_code: str,
        node: ast.AST,
        *,
        path: tuple[str, ...] | None = None,
        evidence: ReviewEvidence | None = None,
    ) -> None:
        location = self.parsed.location(node)
        snippet, truncated = _bounded_snippet(str(location["syntax"]))
        self.collector._add(
            ReviewCandidate(
                rule_id,
                condition_code,
                self.parsed.member_id,
                self.parsed.source_id,
                int(location["line"]),
                self.parsed.cell,
                snippet,
                truncated,
                self.collector._next_occurrence(),
                path,
                evidence,
            )
        )

    def _collect_sys_path(self, call: ast.Call) -> None:
        if (
            len(call.args) != 1
            or call.keywords
            or not isinstance(call.func, ast.Attribute)
            or call.func.attr != "append"
            or not isinstance(call.func.value, ast.Name)
        ):
            return
        binding = self._lookup(call.func.value.id)
        literal = _literal_string(call.args[0])
        if binding is None or binding.kind != "sys_path" or literal is None:
            return
        if "..." not in re.split(r"[/\\]", literal):
            return
        self._add_ast_candidate(_SYS_PATH_RULE, "three_dot_path_segment", call)

    def _collect_open(self, call: ast.Call) -> None:
        if not isinstance(call.func, ast.Name) or call.func.id != "open":
            return
        if not self._builtin_available("open"):
            return
        selected = _open_arguments(call)
        if selected is None:
            return
        path_node, mode = selected
        if mode is not None and not _read_mode(mode):
            return
        normalized = None
        literal = _literal_string(path_node)
        if literal is not None:
            normalized = normalize_relative_posix(literal)
        elif isinstance(path_node, ast.Name):
            binding = self._lookup(path_node.id)
            if (
                binding is not None
                and binding.kind == "literal_path"
                and binding.depth == 0
                and isinstance(binding.value, tuple)
            ):
                normalized = binding.value
        if normalized is None:
            return
        self._add_ast_candidate(
            _OPEN_RULE,
            "read_path_only_in_bundled_archive",
            call,
            path=normalized,
        )

    def _collect_pandas(self, call: ast.Call) -> None:
        if (
            not call.args
            or isinstance(call.args[0], ast.Starred)
            or not isinstance(call.func, ast.Attribute)
            or call.func.attr != "read_csv"
            or not isinstance(call.func.value, ast.Name)
        ):
            return
        binding = self._lookup(call.func.value.id)
        if binding is None or binding.kind != "pandas":
            return
        operand = call.args[0]
        literal = _literal_string(operand)
        normalized = None if literal is None else normalize_relative_posix(literal)
        if normalized is None and isinstance(operand, ast.Name):
            path_binding = self._lookup(operand.id)
            if (
                path_binding is not None
                and path_binding.kind == "literal_path"
                and path_binding.depth == 0
                and isinstance(path_binding.value, tuple)
            ):
                normalized = path_binding.value
        if normalized is None:
            return
        self._add_ast_candidate(
            _PANDAS_RULE,
            "pandas_csv_not_in_inventory",
            call,
            path=normalized,
        )

    def _collect_gdown(self, call: ast.Call) -> None:
        if (
            not call.args
            or isinstance(call.args[0], ast.Starred)
            or not isinstance(call.func, ast.Attribute)
            or call.func.attr != "download"
            or not isinstance(call.func.value, ast.Name)
        ):
            return
        binding = self._lookup(call.func.value.id)
        if binding is None or binding.kind != "gdown":
            return
        evidence = self._anon_expression(call.args[0])
        if evidence is None:
            return
        self._add_ast_candidate(
            _GDOWN_RULE,
            "anonymized_download_identifier",
            call,
            evidence=evidence,
        )

    def _collect_entry_inputs(self, body: Sequence[ast.stmt]) -> None:
        if not self._builtin_available("input"):
            return
        finder = _EntryInputFinder()
        for statement in body:
            finder.visit(statement)
        for call in finder.calls:
            self._add_ast_candidate(_ENTRY_RULE, "entry_point_stdin", call)

    def _collect_gfile(self, call: ast.Call) -> None:
        if not call.args or isinstance(call.args[0], ast.Starred):
            return
        if not self._is_gfile_callee(call.func):
            return
        leading = _literal_string(call.args[0])
        if leading is None and isinstance(call.args[0], ast.Call):
            join = call.args[0]
            if (
                join.args
                and not isinstance(join.args[0], ast.Starred)
                and self._is_join(join.func)
            ):
                leading = _literal_string(join.args[0])
        if leading is None or not _bucket_authority(leading):
            return
        self._add_ast_candidate(_GFILE_RULE, "bucket_authority_literal", call)

    def _is_gfile_callee(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            binding = self._lookup(node.id)
            return binding is not None and binding.kind == "gfile"
        if not isinstance(node, ast.Attribute) or node.attr != "GFile":
            return False
        if isinstance(node.value, ast.Name):
            binding = self._lookup(node.value.id)
            return binding is not None and binding.kind == "tensorflow_gfile"
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "gfile"
            and isinstance(value.value, ast.Attribute)
            and value.value.attr == "io"
            and isinstance(value.value.value, ast.Name)
        ):
            binding = self._lookup(value.value.value.id)
            return binding is not None and binding.kind == "tensorflow"
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "gfile"
            and isinstance(value.value, ast.Name)
        ):
            binding = self._lookup(value.value.id)
            return binding is not None and binding.kind == "tensorflow_io"
        return False

    def _is_join(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            binding = self._lookup(node.id)
            return binding is not None and binding.kind == "os_join"
        if not isinstance(node, ast.Attribute) or node.attr != "join":
            return False
        if isinstance(node.value, ast.Name):
            binding = self._lookup(node.value.id)
            return binding is not None and binding.kind == "os_path"
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "path"
            and isinstance(node.value.value, ast.Name)
        ):
            binding = self._lookup(node.value.value.id)
            return binding is not None and binding.kind == "os"
        return False


def _canonical_entry_guard(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "__name__"
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
        and _literal_string(node.comparators[0]) == "__main__"
    )


def _open_arguments(call: ast.Call) -> tuple[ast.expr, str | None] | None:
    if any(keyword.arg is None for keyword in call.keywords):
        return None
    keywords = {keyword.arg: keyword.value for keyword in call.keywords}
    if len(keywords) != len(call.keywords) or set(keywords) - {"file", "mode"}:
        return None
    if len(call.args) > 2 or any(
        isinstance(argument, ast.Starred) for argument in call.args
    ):
        return None
    if call.args and "file" in keywords:
        return None
    if len(call.args) > 1 and "mode" in keywords:
        return None
    path = call.args[0] if call.args else keywords.get("file")
    if path is None:
        return None
    mode_node = call.args[1] if len(call.args) == 2 else keywords.get("mode")
    if mode_node is None:
        return path, None
    mode = _literal_string(mode_node)
    if mode is None:
        return None
    return path, mode


def _read_mode(mode: str) -> bool:
    return (
        bool(mode)
        and set(mode) <= {"r", "b", "t", "+"}
        and mode.count("r") == 1
        and mode.count("b") <= 1
        and mode.count("t") <= 1
        and not ({"b", "t"} <= set(mode))
        and mode.count("+") <= 1
    )


def _bucket_authority(value: str) -> bool:
    if not value.startswith("gs://"):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "gs" and parsed.netloc == "bucket"


def _normalized_member_path(member: Mapping[str, object]) -> tuple[str, ...] | None:
    return normalize_relative_posix(str(member["name"]))


def _member_maps(
    members: Sequence[Mapping[str, object]],
) -> tuple[
    dict[str, Mapping[str, object]],
    dict[str, str],
    dict[str, list[tuple[tuple[str, ...], Mapping[str, object]]]],
]:
    by_id = {str(member["member_id"]): member for member in members}
    container_parent: dict[str, str] = {}
    paths: dict[str, list[tuple[tuple[str, ...], Mapping[str, object]]]] = {}
    for member in members:
        container_id = str(member["container_id"])
        parent = member["parent_member_id"]
        if parent is not None:
            container_parent[container_id] = str(parent)
        normalized = _normalized_member_path(member)
        if normalized is not None:
            paths.setdefault(container_id, []).append((normalized, member))
    return by_id, container_parent, paths


def _descends_from(
    container_id: str,
    ancestor_id: str,
    by_id: Mapping[str, Mapping[str, object]],
    container_parent: Mapping[str, str],
) -> bool:
    current = container_id
    seen: set[str] = set()
    while current != ancestor_id and current not in seen:
        seen.add(current)
        parent_member_id = container_parent.get(current)
        if parent_member_id is None:
            return False
        parent = by_id.get(parent_member_id)
        if parent is None:
            return False
        current = str(parent["container_id"])
    return current == ancestor_id and container_id != ancestor_id


def _readable_regular(member: Mapping[str, object]) -> bool:
    return member["kind"] == "file" and member["read_status"] in {
        "not_read",
        "complete",
    }


def _resolve_open_candidates(
    candidates: Iterable[ReviewCandidate],
    members: Sequence[Mapping[str, object]],
) -> list[ReviewCandidate]:
    by_id, container_parent, paths = _member_maps(members)
    resolved: list[ReviewCandidate] = []
    for candidate in candidates:
        if candidate.member_id is None or candidate.path is None:
            continue
        source_member = by_id.get(candidate.member_id)
        if source_member is None:
            continue
        source_container = str(source_member["container_id"])
        if any(path == candidate.path for path, _ in paths.get(source_container, [])):
            continue
        matches = [
            member
            for container_id, entries in paths.items()
            if _descends_from(container_id, source_container, by_id, container_parent)
            for path, member in entries
            if path == candidate.path and _readable_regular(member)
        ]
        if len(matches) != 1:
            continue
        match = matches[0]
        evidence = ReviewEvidence(
            ("bundled_archive_member", str(match["member_id"])),
            "bundled_archive_member",
            str(match["member_id"]),
            None,
            None,
            None,
            str(match["name"])[:4096],
        )
        resolved.append(replace(candidate, evidence=evidence))
    return resolved


def _resolve_pandas_candidates(
    candidates: Iterable[ReviewCandidate],
    members: Sequence[Mapping[str, object]],
) -> list[ReviewCandidate]:
    by_id, _, paths = _member_maps(members)
    resolved: list[ReviewCandidate] = []
    for candidate in candidates:
        if candidate.member_id is None or candidate.path is None:
            continue
        source_member = by_id.get(candidate.member_id)
        if source_member is None:
            continue
        container = str(source_member["container_id"])
        source_path = _normalized_member_path(source_member)
        if source_path is None:
            continue
        expected = {candidate.path, (*source_path[:-1], *candidate.path)}
        if any(path in expected for path, _ in paths.get(container, [])):
            continue
        resolved.append(candidate)
    return resolved


def applicable_source_records(
    rule_id: str,
    detected_kind: str,
    source_index: Sequence[Mapping[str, object]],
    collector: ReviewCollector,
) -> tuple[bool, list[ReviewGap]]:
    """Return rule-specific applicability and exact source-inspection gaps."""

    if detected_kind.startswith("unsupported_"):
        return True, [
            ReviewGap(
                None,
                None,
                "unsupported_format",
                "The top-level regular file is not a supported Python input.",
                False,
            )
        ]
    if rule_id in {_OPEN_RULE, _PANDAS_RULE} and detected_kind != "zip":
        return False, []

    applicable = False
    gaps = list(collector.extra_gaps[rule_id])
    gap_keys = {
        (gap.member_id, gap.source_id, gap.reason_code, gap.failed) for gap in gaps
    }
    for source in source_index:
        form = str(source["form"])
        status = str(source["status"])
        source_id = str(source["source_id"])
        member_id = None if source["member_id"] is None else str(source["member_id"])
        relevant = False
        examined = False
        if rule_id == _COMMENT_RULE:
            relevant = form in {"python_file", "notebook_code_cell"}
            examined = source_id in collector.lexical_examined
        elif rule_id == _PIP_RULE:
            relevant = form == "notebook_code_cell"
            examined = source_id in collector.shell_examined
        elif rule_id in _FILE_AST_RULES or rule_id == _PANDAS_RULE:
            relevant = form == "python_file"
            examined = status == "inspected"
        elif rule_id in _SHARED_AST_RULES:
            relevant = form in {"python_file", "notebook_code_cell"}
            examined = status == "inspected"
        if relevant:
            applicable = True
            if examined:
                continue
            reason = str(source["reason_code"] or "source_inspection_error")
            failed = status == "error"
            key = (member_id, source_id, reason, failed)
            if key not in gap_keys:
                gaps.append(
                    ReviewGap(
                        member_id,
                        source_id,
                        reason,
                        (
                            "This applicable Python input failed during rule inspection."
                            if failed
                            else "This applicable Python input was skipped during rule inspection."
                        ),
                        failed,
                    )
                )
                gap_keys.add(key)
        if (
            form == "notebook_document"
            and status == "error"
            and rule_id in _NOTEBOOK_RULES
        ):
            applicable = True
    if collector.extra_gaps[rule_id]:
        applicable = True
    return applicable, sorted(gaps, key=ReviewGap.sort_key)

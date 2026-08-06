"""Versioned deterministic content summarisers (the evidence stage).

Turns a routed file's bytes into compact, **line-numbered**, citable evidence.
Every rule is fixed in code and stamped with :data:`EXTRACT_VERSION`, so the
same archive yields byte-identical evidence on every run — the property that
lets a rubric change re-grade from the same evidence without re-reading
archives, and keeps the temperature-0 Validation call reproducible.

File-type router:

| kind       | files                                   | extraction                          |
|------------|-----------------------------------------|-------------------------------------|
| manifest   | requirements/lockfiles/Dockerfile/yaml… | verbatim, head+tail if over cap     |
| shell      | ``*.sh`` / Makefile                     | verbatim, head+tail if over cap     |
| python     | ``*.py``                                | verbatim ≤ cap; else AST-compressed |
| notebook   | ``*.ipynb``                             | drop outputs; md + code-as-python   |
| readme     | ``README*``                             | verbatim (Documentation channel src)|
| data_stat  | ``*.csv``/``*.npy``/data dirs           | path + size (+ header if tiny)      |
| other      | ``*.R``/``*.jl``/``*.m``/…              | verbatim head+tail (no AST in v1)   |

Truncation always preserves **true line numbers** (so a reader can cite ranges)
and never silently drops the middle — gaps are marked ``… N lines elided …``.
Token counts use a zero-dependency ``chars / 4`` estimate.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass

from . import routing

EXTRACT_VERSION = "extract-v1"

# Per-file token budget (~1.5k tokens) for cached evidence content.
PER_FILE_TOKEN_CAP = 1500

# The README carries the Documentation channel for all four stages, so it gets a larger
# cap than an ordinary file.
README_TOKEN_CAP = 4000

# Data files small enough to peek at a header (existence is otherwise the signal).
DATA_HEADER_MAX_BYTES = 64 * 1024
DATA_HEADER_LINES = 8
DATA_HEADER_EXTS = frozenset({".csv", ".tsv", ".json", ".jsonl"})

# Call-sites kept verbatim in AST-compressed Python — the machine-actionable
# signal each stage needs (argparse, output writers, checkpoints, subprocess,
# data acquisition). Matched as a substring of the call's dotted name.
_CALL_PATTERNS: tuple[str, ...] = (
    "argumentparser",
    "add_argument",
    "parse_args",
    "plt.",
    "pyplot",
    "savefig",
    "sns.",
    "imsave",
    ".to_csv",
    "np.save",
    "np.savez",
    "np.savetxt",
    "numpy.save",
    "json.dump",
    "savetxt",
    "write_csv",
    "torch.save",
    "torch.load",
    "load_state_dict",
    "save_pretrained",
    "subprocess.",
    "os.system",
    "check_call",
    "check_output",
    "load_dataset",
    "urlretrieve",
    "urllib",
    "requests.get",
    "wget",
    "download",
    "from_pretrained",
    "hf_hub_download",
    "load_data",
)


@dataclass(frozen=True)
class Extracted:
    kind: str
    content: str
    n_lines_orig: int
    truncated: bool


def est_tokens(text: str) -> int:
    """Zero-dependency token estimate (chars / 4)."""
    return len(text) // 4


# --- file-type router --------------------------------------------------------


def kind_for(path: str) -> str:
    """Map a path to an extraction kind (independent of the rubric cell)."""
    if routing.is_renderable_readme(path) or routing.basename(path).lower().startswith(
        "readme"
    ):
        return "readme"
    base = routing.basename(path).lower()
    ext = routing.extension(base)
    if ext == ".ipynb":
        return "notebook"
    if ext == ".py":
        return "python"
    if ext == ".sh" or base in ("makefile", "gnumakefile", "snakefile"):
        return "shell"
    if _is_manifest(base, ext):
        return "manifest"
    if ext in routing.DATA_EXTS:
        return "data_stat"
    if ext in routing.CODE_EXTS:
        return "other"
    return "other"


def _is_manifest(base: str, ext: str) -> bool:
    if ext in (".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".lock", ".gin"):
        return True
    if base.startswith(("requirements", "environment", "conda", "pipfile")):
        return True
    return base in (
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "dockerfile",
        "renv.lock",
        "description",
    ) or base.startswith(("dockerfile", "singularity"))


# --- public entry point ------------------------------------------------------


def extract_evidence(
    path: str, data: bytes | None, size: int, token_cap: int = PER_FILE_TOKEN_CAP
) -> Extracted:
    """Summarise one file into citable evidence.

    ``data`` is the (capped) member bytes, or ``None`` when the caller chose not
    to read them (large data files — existence is the signal).
    """
    kind = kind_for(path)
    if kind == "data_stat":
        return _extract_data(path, data, size)
    if data is None:
        return Extracted(
            kind=kind, content=_path_size(path, size), n_lines_orig=0, truncated=False
        )
    text = _decode(data)
    lines = text.split("\n")
    if kind == "python":
        return _extract_python(text, lines, token_cap)
    if kind == "notebook":
        return _extract_notebook(text, token_cap)
    # manifest / shell / readme / other → verbatim (head+tail if over cap). The
    # README carries the Documentation channel, so it keeps a larger budget.
    cap = README_TOKEN_CAP if kind == "readme" else token_cap
    content, truncated = _verbatim(lines, cap)
    return Extracted(
        kind=kind, content=content, n_lines_orig=len(lines), truncated=truncated
    )


# --- decoding / rendering ----------------------------------------------------


def _decode(data: bytes) -> str:
    return (
        data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    )


def _render(lines: list[str], keep: set[int]) -> tuple[str, bool]:
    """Render kept 1-based line numbers, marking elided gaps. ``truncated`` is
    True iff any line was dropped."""
    if not keep:
        return "", bool(lines)
    kept = sorted(n for n in keep if 1 <= n <= len(lines))
    out: list[str] = []
    prev: int | None = None
    for n in kept:
        if prev is not None and n > prev + 1:
            gap = n - prev - 1
            out.append(f"      … {gap} line{'s' if gap != 1 else ''} elided …")
        out.append(f"{n}: {lines[n - 1]}")
        prev = n
    return "\n".join(out), len(kept) < len(lines)


def _verbatim(lines: list[str], token_cap: int) -> tuple[str, bool]:
    """Whole file line-numbered, or head+tail within budget if over cap."""
    full, _ = _render(lines, set(range(1, len(lines) + 1)))
    if est_tokens(full) <= token_cap:
        return full, False
    keep = _head_tail_lines(lines, token_cap)
    return _render(lines, keep)


def _head_tail_lines(lines: list[str], token_cap: int) -> set[int]:
    """Greedily keep head (~70% budget) + tail (~30%) by character count."""
    char_budget = token_cap * 4
    head_budget = int(char_budget * 0.7)
    keep: set[int] = set()
    used = 0
    i = 0
    while i < len(lines) and used < head_budget:
        keep.add(i + 1)
        used += len(lines[i]) + 1
        i += 1
    used = 0
    j = len(lines) - 1
    tail_budget = char_budget - head_budget
    while j >= i and used < tail_budget:
        keep.add(j + 1)
        used += len(lines[j]) + 1
        j -= 1
    return keep


def _path_size(path: str, size: int) -> str:
    return f"[file] {path} ({size if size >= 0 else '?'} bytes)"


# --- data_stat ---------------------------------------------------------------


def _extract_data(path: str, data: bytes | None, size: int) -> Extracted:
    summary = _path_size(path, size)
    ext = routing.extension(routing.basename(path).lower())
    if (
        data is not None
        and 0 <= size <= DATA_HEADER_MAX_BYTES
        and ext in DATA_HEADER_EXTS
    ):
        head = _decode(data).split("\n")[:DATA_HEADER_LINES]
        body = "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(head))
        return Extracted(
            kind="data_stat",
            content=f"{summary}\n{body}",
            n_lines_orig=len(head),
            truncated=True,
        )
    return Extracted(kind="data_stat", content=summary, n_lines_orig=0, truncated=False)


# --- python (AST compression) ------------------------------------------------


def _extract_python(text: str, lines: list[str], token_cap: int) -> Extracted:
    full, _ = _render(lines, set(range(1, len(lines) + 1)))
    if est_tokens(full) <= token_cap:
        return Extracted(
            kind="python", content=full, n_lines_orig=len(lines), truncated=False
        )
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Py2 / malformed → fall back to verbatim head+tail.
        content, truncated = _verbatim(lines, token_cap)
        return Extracted(
            kind="python", content=content, n_lines_orig=len(lines), truncated=truncated
        )

    keep = _python_skeleton_lines(tree)
    # If the skeleton itself blows the budget, head+tail it.
    skeleton, _ = _render(lines, keep)
    if est_tokens(skeleton) > token_cap:
        keep &= _head_tail_lines(lines, token_cap) | _python_signature_lines(tree)
    content, _ = _render(lines, keep)
    return Extracted(
        kind="python", content=content, n_lines_orig=len(lines), truncated=True
    )


def _line_span(node: ast.AST) -> set[int]:
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", start)
    if start is None:
        return set()
    return set(range(start, (end or start) + 1))


def _python_signature_lines(tree: ast.Module) -> set[int]:
    """Just the def/class headers + decorators (the minimal skeleton)."""
    keep: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            keep |= _def_header_lines(node)
    return keep


def _def_header_lines(node: ast.AST) -> set[int]:
    keep: set[int] = set()
    for dec in getattr(node, "decorator_list", []):
        keep |= _line_span(dec)
    header_start = node.lineno  # type: ignore[attr-defined]
    body = getattr(node, "body", [])
    header_end = body[0].lineno - 1 if body else node.lineno  # type: ignore[attr-defined]
    keep |= set(range(header_start, max(header_start, header_end) + 1))
    # First docstring line, if present.
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        keep |= {body[0].lineno}
    return keep


def _python_skeleton_lines(tree: ast.Module) -> set[int]:
    """Module docstring + imports + top-level config assigns + def/class
    headers + whitelisted call-site statements."""
    keep: set[int] = set()

    # Module docstring (first 2 lines).
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        ds = tree.body[0]
        keep |= set(
            range(
                ds.lineno, min(getattr(ds, "end_lineno", ds.lineno), ds.lineno + 1) + 1
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            keep |= _line_span(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # Top-level config constants — keep the first few lines of the value.
            span = sorted(_line_span(node))
            keep |= set(span[:3])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            keep |= _def_header_lines(node)

    # Nested def/class headers + every whitelisted call-site, anywhere.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            keep |= _def_header_lines(node)
        elif isinstance(node, ast.Call) and _is_whitelisted_call(node):
            keep |= _line_span(node)
    return keep


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts)).lower()


def _is_whitelisted_call(node: ast.Call) -> bool:
    dotted = _dotted_name(node.func)
    if any(pat in dotted for pat in _CALL_PATTERNS):
        return True
    # open(..., 'w'/'a') — a write-mode file open is an output signal.
    if dotted == "open":
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if any(m in arg.value for m in ("w", "a", "x")):
                    return True
    return False


# --- notebook ----------------------------------------------------------------


def _extract_notebook(text: str, token_cap: int) -> Extracted:
    try:
        nb = json.loads(text)
        cells = nb.get("cells", [])
    except (json.JSONDecodeError, AttributeError):
        lines = text.split("\n")
        content, truncated = _verbatim(lines, token_cap)
        return Extracted(
            kind="notebook",
            content=content,
            n_lines_orig=len(lines),
            truncated=truncated,
        )

    md_src: list[str] = []
    code_src: list[str] = []
    for cell in cells:
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        if cell.get("cell_type") == "markdown":
            md_src.append(src.strip())
        elif cell.get("cell_type") == "code":
            code_src.append(src)

    md_text = "\n".join(s for s in md_src if s)
    code_text = "\n".join(code_src)
    n_orig = md_text.count("\n") + code_text.count("\n") + 2

    # Budget split: markdown narrative gets ~30%, code (compressed) ~70%.
    md_lines = md_text.split("\n")
    md_rendered, md_trunc = _verbatim(md_lines, int(token_cap * 0.3))
    code_ex = _extract_python(code_text, code_text.split("\n"), int(token_cap * 0.7))

    content = (
        "## markdown cells\n"
        + md_rendered
        + "\n## code cells (outputs dropped)\n"
        + code_ex.content
    )
    return Extracted(
        kind="notebook",
        content=content,
        n_lines_orig=n_orig,
        truncated=md_trunc or code_ex.truncated,
    )

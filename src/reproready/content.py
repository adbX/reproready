"""Targeted archive-bytes reader for the evidence stage.

:mod:`reproready.inventory` lists archive entries but never reads member bytes.
The evidence stage needs the *content* of a small, screen-selected set of paths.
This module returns those bytes — for zip / tar.gz / bare-dir inputs, including
the single-level nested-archive case the inventory models (a member path like
``code.zip/main.py``).

Every read is capped at :data:`MEMBER_CAP_BYTES` so a target that turns out to
be a bundled model checkpoint can never pull megabytes into the prompt. The
caller passes the exact paths the screen recorded as pointers, so reads stay
targeted — we never decompress the whole archive into memory at once.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from .inventory import NESTED_SUFFIXES

# Hard ceiling on any single member read (2 MB). Text evidence is line-budgeted
# downstream; this only stops a checkpoint/data blob from being slurped.
MEMBER_CAP_BYTES = 2 * 1024 * 1024


def read_members(
    path: Path, ext_hint: str, targets: set[str], cap: int = MEMBER_CAP_BYTES
) -> dict[str, bytes]:
    """Return ``{target: bytes}`` for the readable members of ``targets``.

    Dispatch mirrors :func:`reproready.inventory.read_archive`. Missing /
    unreadable members are simply absent from the result (never raises for one
    bad member). A directory ``path`` is read as a working tree.
    """
    if not targets:
        return {}
    if path.is_dir():
        return _read_dir_members(path, targets, cap)
    if not path.exists():
        return {}

    ext = ext_hint.lower()
    if ext == ".zip" or (not ext and zipfile.is_zipfile(path)):
        return _read_zip_members(path, targets, cap)
    if ext in (".tar.gz", ".tgz"):
        return _read_targz_members(path, targets, cap)
    # Best-effort sniff for unknown extensions.
    if zipfile.is_zipfile(path):
        return _read_zip_members(path, targets, cap)
    try:
        if tarfile.is_tarfile(path):
            return _read_targz_members(path, targets, cap)
    except OSError:
        pass
    return {}


# --- nested-path splitting ---------------------------------------------------


def _split_nested(target: str) -> tuple[str | None, str]:
    """Split ``outer.zip/inner/path`` → ``("outer.zip", "inner/path")``.

    Returns ``(None, target)`` when the path has no nested-archive prefix.
    Uses the earliest nested-suffix boundary, matching the single-level
    recursion in :mod:`reproready.inventory`.
    """
    lower = target.lower()
    best: tuple[int, int] | None = None  # (boundary index, suffix length)
    for suf in NESTED_SUFFIXES:
        token = suf + "/"
        idx = lower.find(token)
        if idx != -1 and (best is None or idx < best[0]):
            best = (idx + len(suf), len(suf))
    if best is None:
        return None, target
    cut = best[0]  # position of the '/' after the archive suffix
    return target[:cut], target[cut + 1 :]


def _group_targets(targets: set[str]) -> tuple[set[str], dict[str, set[str]]]:
    """Partition into direct members and ``{outer_archive: {inner_paths}}``."""
    direct: set[str] = set()
    nested: dict[str, set[str]] = {}
    for t in targets:
        outer, inner = _split_nested(t)
        if outer is None:
            direct.add(t)
        else:
            nested.setdefault(outer, set()).add(inner)
    return direct, nested


# --- zip ---------------------------------------------------------------------


def _read_zip_members(path: Path, targets: set[str], cap: int) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    direct, nested = _group_targets(targets)
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for t in direct:
                if t in names:
                    out[t] = _read_one_zip(zf, t, cap)
            for outer, inners in nested.items():
                if outer not in names:
                    continue
                blob = _read_one_zip(zf, outer, MEMBER_CAP_BYTES)
                for inner, data in _read_inner(outer, blob, inners, cap).items():
                    out[f"{outer}/{inner}"] = data
    except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError):
        return out
    return out


def _read_one_zip(zf: zipfile.ZipFile, name: str, cap: int) -> bytes:
    with zf.open(name) as fh:
        return fh.read(cap)


# --- tar.gz ------------------------------------------------------------------


def _read_targz_members(path: Path, targets: set[str], cap: int) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    direct, nested = _group_targets(targets)
    nested_blobs: dict[str, bytes] = {}
    try:
        with tarfile.open(path, mode="r|gz") as tf:
            for member in tf:
                name = member.name.replace("\\", "/").lstrip("./").lstrip("/")
                if not member.isfile():
                    continue
                if name in direct:
                    fh = tf.extractfile(member)
                    if fh is not None:
                        out[name] = fh.read(cap)
                elif name in nested:
                    fh = tf.extractfile(member)
                    if fh is not None:
                        nested_blobs[name] = fh.read(MEMBER_CAP_BYTES)
    except (tarfile.TarError, OSError, EOFError):
        return out
    for outer, blob in nested_blobs.items():
        for inner, data in _read_inner(outer, blob, nested[outer], cap).items():
            out[f"{outer}/{inner}"] = data
    return out


# --- inner archive (single level) -------------------------------------------


def _read_inner(
    outer: str, blob: bytes, inners: set[str], cap: int
) -> dict[str, bytes]:
    buf = io.BytesIO(blob)
    lower = outer.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(buf) as zf:
                names = set(zf.namelist())
                return {i: _read_one_zip(zf, i, cap) for i in inners if i in names}
        if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
            out: dict[str, bytes] = {}
            with tarfile.open(fileobj=buf, mode="r|gz") as tf:
                for member in tf:
                    name = member.name.replace("\\", "/").lstrip("./").lstrip("/")
                    if member.isfile() and name in inners:
                        fh = tf.extractfile(member)
                        if fh is not None:
                            out[name] = fh.read(cap)
            return out
    except (
        zipfile.BadZipFile,
        tarfile.TarError,
        OSError,
        EOFError,
        NotImplementedError,
    ):
        return {}
    return {}


# --- directory working tree --------------------------------------------------


def _read_dir_members(root: Path, targets: set[str], cap: int) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for t in targets:
        # Guard against traversal / absolute paths in the inventory.
        if t.startswith("/") or ".." in t.split("/"):
            continue
        fp = root / t
        try:
            if fp.is_file():
                with fp.open("rb") as fh:
                    out[t] = fh.read(cap)
        except OSError:
            continue
    return out

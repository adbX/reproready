"""Raw, uncapped archive/directory reader — the file inventory.

Lists the entries of a code artifact verbatim: no depth cap, no wrapper strip,
no dedupe. Every path the artifact ships is preserved as ground truth for the
scorer's routing and scope stages.

Single-level nested-archive recursion: when an outer entry's name ends in
``.zip`` / ``.tar.gz`` / ``.tgz`` the bytes are re-opened and the inner listing
is yielded with the outer entry's path prepended. Exactly one level deep — a
nested zip-in-zip-in-zip surfaces its inner-inner payload as a single opaque
filename.
"""

from __future__ import annotations

import io
import os
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Suffixes that trigger single-level nested-archive expansion. Public so the
# targeted byte reader (:mod:`reproready.content`) shares one definition.
NESTED_SUFFIXES: tuple[str, ...] = (".zip", ".tar.gz", ".tgz")

# Always pruned when walking a directory tree: a `.git/` subtree is version-
# control metadata, not artifact content, and including it would swamp the
# inventory with thousands of object-store paths. Everything else an author
# committed is kept.
_PRUNE_DIRS: frozenset[str] = frozenset({".git"})


@dataclass(frozen=True)
class RawEntry:
    path: str  # POSIX, verbatim — no depth cap, no prefix strip, no dedupe
    size: int  # uncompressed if known, else -1
    is_dir: bool
    depth: int  # 1-based; for nested entries, outer_depth + inner_depth


@dataclass
class ReadResult:
    archive_kind: str  # zip / targz / gz / pdf / dir / none
    entries: list[RawEntry] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def read_archive(path: Path, ext_hint: str) -> ReadResult:
    """Return a :class:`ReadResult` for ``path``.

    ``ext_hint`` is the artifact's known extension (``.zip``, ``.tar.gz``,
    ``.pdf``, ``.gz``, ``""``) and drives dispatch so we never sniff magic
    bytes for happy-path archives. Missing files surface as
    ``archive_kind='none'`` with an empty listing.
    """
    ext = ext_hint.lower()

    if not path.exists():
        kind = "pdf" if ext == ".pdf" else "none"
        return ReadResult(archive_kind=kind)

    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".zip":
        return _read_zip(path)
    if ext in (".tar.gz", ".tgz"):
        return _read_targz(path)
    if ext == ".gz":
        return _read_gz(path)

    # Unknown extension — best-effort sniff by content.
    if path.is_file() and zipfile.is_zipfile(path):
        return _read_zip(path)
    try:
        if path.is_file() and tarfile.is_tarfile(path):
            return _read_targz(path)
    except OSError:
        pass
    return ReadResult(archive_kind="none", error=f"unrecognized extension {ext_hint!r}")


def iter_entries(path: Path, ext_hint: str) -> Iterator[RawEntry]:
    """Iterator shortcut for callers that only care about the entries."""
    yield from read_archive(path, ext_hint).entries


def read_dir(path: Path) -> ReadResult:
    """Walk a directory tree and emit one entry per file/dir.

    ``.git/`` is pruned (version-control metadata, not artifact content). All
    other paths, including any noisy build artefacts, are kept verbatim.
    """
    if not path.exists():
        return ReadResult(archive_kind="dir", error=f"missing dir: {path}")
    if not path.is_dir():
        return ReadResult(archive_kind="dir", error=f"not a directory: {path}")

    entries: list[RawEntry] = []
    root_str = str(path)
    try:
        for cur_root, dirs, files in os.walk(root_str, followlinks=False):
            dirs[:] = [d for d in dirs if d not in _PRUNE_DIRS]
            rel_root = os.path.relpath(cur_root, root_str).replace(os.sep, "/")
            prefix = "" if rel_root == "." else f"{rel_root}/"
            for d in dirs:
                rel = f"{prefix}{d}"
                depth = rel.count("/") + 1
                entries.append(
                    RawEntry(
                        path=f"{rel}/",
                        size=0,
                        is_dir=True,
                        depth=depth,
                    )
                )
            for f in files:
                rel = f"{prefix}{f}"
                depth = rel.count("/") + 1
                try:
                    size = os.stat(
                        os.path.join(cur_root, f), follow_symlinks=False
                    ).st_size
                except OSError:
                    size = -1
                entries.append(
                    RawEntry(
                        path=rel,
                        size=size,
                        is_dir=False,
                        depth=depth,
                    )
                )
    except OSError as exc:
        return ReadResult(
            archive_kind="dir", entries=entries, error=f"{type(exc).__name__}: {exc}"
        )
    return ReadResult(archive_kind="dir", entries=entries)


# ---------------------------------------------------------------------------
# dispatchers
# ---------------------------------------------------------------------------


def _read_pdf(path: Path) -> ReadResult:
    size = _safe_size(path)
    return ReadResult(
        archive_kind="pdf",
        entries=[RawEntry(path=path.name, size=size, is_dir=False, depth=1)],
    )


def _read_gz(path: Path) -> ReadResult:
    # Bare .gz that isn't a tarball — synthesize a single entry for its stem.
    inner = path.stem or path.name
    size = _safe_size(path)
    return ReadResult(
        archive_kind="gz",
        entries=[RawEntry(path=inner, size=size, is_dir=False, depth=1)],
    )


def _read_zip(path: Path) -> ReadResult:
    try:
        with zipfile.ZipFile(path) as zf:
            outer = [_zip_entry(info) for info in zf.infolist() if info.filename]
            nested = _expand_nested_from_zip(zf, outer)
    except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError) as exc:
        return ReadResult(archive_kind="zip", error=f"{type(exc).__name__}: {exc}")

    return ReadResult(archive_kind="zip", entries=outer + nested)


def _read_targz(path: Path) -> ReadResult:
    # Tar streams only let you read each member as you encounter it, so we
    # materialize inner-archive bytes on the first pass and expand after.
    outer: list[RawEntry] = []
    inner_blobs: list[tuple[RawEntry, bytes]] = []
    try:
        with tarfile.open(path, mode="r|gz") as tf:
            for member in tf:
                if not member.name:
                    continue
                entry = _tar_entry(member)
                outer.append(entry)
                if not entry.is_dir and _is_nested(entry.path):
                    fh = tf.extractfile(member)
                    if fh is not None:
                        inner_blobs.append((entry, fh.read()))
    except (tarfile.TarError, OSError, EOFError) as exc:
        return ReadResult(archive_kind="targz", error=f"{type(exc).__name__}: {exc}")

    nested: list[RawEntry] = []
    for outer_entry, blob in inner_blobs:
        nested.extend(_expand_inner_bytes(outer_entry, blob))
    return ReadResult(archive_kind="targz", entries=outer + nested)


# ---------------------------------------------------------------------------
# nested-archive expansion (single level)
# ---------------------------------------------------------------------------


def _expand_nested_from_zip(
    zf: zipfile.ZipFile, outer_entries: list[RawEntry]
) -> list[RawEntry]:
    expanded: list[RawEntry] = []
    for outer in outer_entries:
        if outer.is_dir or not _is_nested(outer.path):
            continue
        try:
            with zf.open(outer.path) as fh:
                blob = fh.read()
        except (zipfile.BadZipFile, OSError, KeyError) as exc:
            expanded.append(_error_entry(outer, f"{type(exc).__name__}: {exc}"))
            continue
        expanded.extend(_expand_inner_bytes(outer, blob))
    return expanded


def _expand_inner_bytes(outer: RawEntry, blob: bytes) -> list[RawEntry]:
    buf = io.BytesIO(blob)
    lower = outer.path.lower()
    if lower.endswith(".zip"):
        return _expand_inner_zip(outer, buf)
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return _expand_inner_targz(outer, buf)
    return []


def _expand_inner_zip(outer: RawEntry, buf: io.BytesIO) -> list[RawEntry]:
    try:
        with zipfile.ZipFile(buf) as inner_zf:
            entries = [
                _prepend(outer, _zip_entry(info))
                for info in inner_zf.infolist()
                if info.filename
            ]
    except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError) as exc:
        return [_error_entry(outer, f"{type(exc).__name__}: {exc}")]
    return entries


def _expand_inner_targz(outer: RawEntry, buf: io.BytesIO) -> list[RawEntry]:
    out: list[RawEntry] = []
    try:
        with tarfile.open(fileobj=buf, mode="r|gz") as inner_tf:
            for member in inner_tf:
                if not member.name:
                    continue
                out.append(_prepend(outer, _tar_entry(member)))
    except (tarfile.TarError, OSError, EOFError) as exc:
        return [_error_entry(outer, f"{type(exc).__name__}: {exc}")]
    return out


def _prepend(outer: RawEntry, inner: RawEntry) -> RawEntry:
    return RawEntry(
        path=f"{outer.path}/{inner.path}",
        size=inner.size,
        is_dir=inner.is_dir,
        depth=outer.depth + inner.depth,
    )


def _error_entry(outer: RawEntry, message: str) -> RawEntry:
    # Synthetic marker so broken inner archives don't silently disappear.
    # The "!ERROR:<message>" tail is unambiguous — no real filename contains "!".
    return RawEntry(
        path=f"{outer.path}!ERROR:{message}",
        size=-1,
        is_dir=False,
        depth=outer.depth,
    )


# ---------------------------------------------------------------------------
# entry constructors
# ---------------------------------------------------------------------------


def _zip_entry(info: zipfile.ZipInfo) -> RawEntry:
    raw = info.filename.replace("\\", "/").lstrip("/")
    is_dir = info.is_dir() or raw.endswith("/")
    trimmed = raw.rstrip("/") if is_dir else raw
    depth = trimmed.count("/") + 1 if trimmed else 1
    path = f"{trimmed}/" if is_dir else trimmed
    return RawEntry(path=path, size=info.file_size, is_dir=is_dir, depth=depth)


def _tar_entry(member: tarfile.TarInfo) -> RawEntry:
    raw = member.name.replace("\\", "/").lstrip("./").lstrip("/")
    is_dir = member.isdir()
    trimmed = raw.rstrip("/")
    depth = trimmed.count("/") + 1 if trimmed else 1
    path = f"{trimmed}/" if is_dir else trimmed
    return RawEntry(
        path=path,
        size=member.size if not is_dir else 0,
        is_dir=is_dir,
        depth=depth,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _is_nested(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(suf) for suf in NESTED_SUFFIXES)


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1

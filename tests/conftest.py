"""Fixtures for the inventory reader tests.

Every archive is built fresh in ``tmp_path`` from a spec dict — no binary blobs
are checked in. The specs here probe the cases a naive walker would flatten or
drop (deep nesting, duplicate entries, wrapper-only layouts) so the tests catch
regressions toward classifier-style behaviour.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from checker_input_fixtures import CheckerInputSet, build_checker_inputs

DEEP_SPEC: dict[str, str | None] = {
    # Depth 1
    "top.txt": "top",
    "wrapper/": None,
    # Depth 2
    "wrapper/README.md": "# deep",
    "wrapper/src/": None,
    # Depth 3
    "wrapper/src/pkg/": None,
    # Depth 4 — a naive depth cap would drop this; the reader must keep it.
    "wrapper/src/pkg/module.py": "x = 1\n",
    # Depth 5 — still required.
    "wrapper/src/pkg/sub/leaf.py": "leaf\n",
}

FLAT_SPEC: dict[str, str | None] = {
    "main.py": "print('hi')\n",
    "README.md": "# flat\n",
}

DUPLICATE_SPEC = [
    ("dup.txt", b"one"),
    ("dup.txt", b"two"),
    ("dup.txt", b"three"),
]


def _build_zip(path: Path, spec: dict[str, str | None]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in spec.items():
            if content is None:
                zf.writestr(zipfile.ZipInfo(name), b"")
            elif isinstance(content, bytes):
                zf.writestr(name, content)
            else:
                zf.writestr(name, content.encode())


def _build_zip_duplicates(path: Path, entries: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries:
            zf.writestr(name, body)


def _build_targz(path: Path, spec: dict[str, str | None]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, content in spec.items():
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            else:
                body = content if isinstance(content, bytes) else content.encode()
                info.size = len(body)
                tf.addfile(info, io.BytesIO(body))


def _build_zip_in_zip(
    outer_path: Path,
    inner_name: str,
    inner_spec: dict[str, str | None],
    extras: dict[str, str | None] | None = None,
) -> None:
    # Build the inner zip in-memory, then drop it inside the outer.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as inner:
        for name, content in inner_spec.items():
            if content is None:
                inner.writestr(zipfile.ZipInfo(name), b"")
            elif isinstance(content, bytes):
                inner.writestr(name, content)
            else:
                inner.writestr(name, content.encode())
    with zipfile.ZipFile(outer_path, "w", zipfile.ZIP_DEFLATED) as outer:
        outer.writestr(inner_name, buf.getvalue())
        for name, content in (extras or {}).items():
            if content is None:
                outer.writestr(zipfile.ZipInfo(name), b"")
            elif isinstance(content, bytes):
                outer.writestr(name, content)
            else:
                outer.writestr(name, content.encode())


@pytest.fixture(scope="session")
def fixtures_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("inventory-fixtures")

    _build_zip(root / "flat.zip", FLAT_SPEC)
    _build_zip(root / "deep.zip", DEEP_SPEC)
    _build_zip_duplicates(root / "dupes.zip", DUPLICATE_SPEC)
    _build_targz(root / "deep.tar.gz", DEEP_SPEC)

    # Empty zip — legal, zero entries.
    with zipfile.ZipFile(root / "empty.zip", "w"):
        pass

    # Broken archive — truncated zip.
    (root / "broken.zip").write_bytes(b"PK\x03\x04truncated-not-a-zip")

    # PDF-only archive.
    (root / "paper.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    # zip-in-zip — outer contains a valid `code.zip` plus a sibling
    # `sibling.txt`, and inner `code.zip` has its own structure.
    _build_zip_in_zip(
        root / "zip-in-zip.zip",
        inner_name="code.zip",
        inner_spec={
            "main.py": "x = 1\n",
            "pkg/": None,
            "pkg/util.py": "y = 2\n",
        },
        extras={"sibling.txt": "top-level sibling\n"},
    )

    # Broken inner zip — outer is valid, inner `code.zip` bytes are garbage.
    with zipfile.ZipFile(root / "broken-inner.zip", "w", zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("code.zip", b"PK\x03\x04garbage")
        outer.writestr("ok.txt", b"still here")

    return root


@pytest.fixture(scope="session")
def checker_inputs(tmp_path_factory: pytest.TempPathFactory) -> CheckerInputSet:
    """Build the frozen checker input matrix once for the test session."""

    return build_checker_inputs(tmp_path_factory.mktemp("checker-inputs"))

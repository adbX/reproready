"""Synthetic inputs for the checker implementation and contract tests."""

from __future__ import annotations

import json
import os
import stat
import struct
import warnings
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

FIXED_ZIP_TIME = (2020, 1, 2, 3, 4, 6)
TIMEOUT_CHECKPOINT = "after_source_read"
MUTATION_CHECKPOINT = "after_snapshot_copy"


@dataclass(frozen=True)
class CheckerInputSet:
    """Paths and deterministic fault checkpoints shared by checker tests."""

    paths: dict[str, Path]
    timeout_checkpoint: str = TIMEOUT_CHECKPOINT
    mutation_checkpoint: str = MUTATION_CHECKPOINT


def _entry(name: str, body: bytes, mode: int = stat.S_IFREG | 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = mode << 16
    return info


def _zip_bytes(
    entries: list[tuple[str, bytes] | tuple[str, bytes, int]],
    *,
    force_zip64: bool = False,
) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for item in entries:
            name, body = item[:2]
            mode = item[2] if len(item) == 3 else stat.S_IFREG | 0o644
            info = _entry(name, body, mode)
            if force_zip64:
                with archive.open(info, "w", force_zip64=True) as member:
                    member.write(body)
            else:
                archive.writestr(info, body)
    return output.getvalue()


def _patch_u16(data: bytes, signature: bytes, offset: int, value: int) -> bytes:
    patched = bytearray(data)
    start = 0
    found = False
    while True:
        index = patched.find(signature, start)
        if index < 0:
            break
        struct.pack_into("<H", patched, index + offset, value)
        start = index + len(signature)
        found = True
    if not found:
        raise ValueError(f"missing ZIP signature {signature!r}")
    return bytes(patched)


def _set_encrypted(data: bytes) -> bytes:
    local = data.find(b"PK\x03\x04")
    central = data.find(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", data, local + 6)[0] | 1
    central_flags = struct.unpack_from("<H", data, central + 8)[0] | 1
    data = _patch_u16(data, b"PK\x03\x04", 6, local_flags)
    return _patch_u16(data, b"PK\x01\x02", 8, central_flags)


def _set_compression(data: bytes, method: int) -> bytes:
    data = _patch_u16(data, b"PK\x03\x04", 8, method)
    return _patch_u16(data, b"PK\x01\x02", 10, method)


def _corrupt_crc(data: bytes, occurrence: int = 0) -> bytes:
    central = -1
    start = 0
    for _ in range(occurrence + 1):
        central = data.find(b"PK\x01\x02", start)
        if central < 0:
            raise ValueError("missing ZIP central-directory entry")
        start = central + 4
    current = struct.unpack_from("<I", data, central + 16)[0]
    patched = bytearray(data)
    struct.pack_into("<I", patched, central + 16, current ^ 0xFFFFFFFF)
    return bytes(patched)


def _corrupt_central_directory(data: bytes) -> bytes:
    patched = bytearray(data)
    central = patched.find(b"PK\x01\x02")
    if central < 0:
        raise ValueError("missing ZIP central-directory signature")
    patched[central : central + 4] = b"BAD!"
    return bytes(patched)


def _notebook(
    *,
    language: str | None = "python",
    language_info: str | None = None,
    nbformat: int = 4,
    cells: list[dict[str, object]] | None = None,
) -> bytes:
    if cells is None:
        cells = [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [
                    {
                        "name": "stdout",
                        "output_type": "stream",
                        "text": "open('/ignore/output-only')\n",
                    }
                ],
                "source": [
                    "import requests\n",
                    "open('/srv/input.csv')\n",
                    "pathlib.Path(r'C:\\notebook\\data')\n",
                    "example = \"open('/ignore/arbitrary-string')\"\n",
                ],
            },
            {
                "cell_type": "raw",
                "metadata": {},
                "source": "open('/ignore/raw-cell')\n",
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": "from . import local\n",
            },
        ]
    metadata: dict[str, object] = {}
    if language is not None:
        metadata["kernelspec"] = {"language": language, "name": language}
    if language_info is not None:
        metadata["language_info"] = {"name": language_info}
    metadata["checker_decoy"] = "open('/ignore/raw-json')"
    payload = {
        "cells": cells,
        "metadata": metadata,
        "nbformat": nbformat,
        "nbformat_minor": 5,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _nested_zip() -> bytes:
    level_four = _zip_bytes([("leaf.py", b"value = 4\n")])
    level_three = _zip_bytes([("four.zip", level_four)])
    level_two = _zip_bytes([("three.zip", level_three)])
    level_one = _zip_bytes([("two.zip", level_two)])
    return level_one


def _dependency_zip() -> bytes:
    nested = _zip_bytes(
        [
            ("inner/requirements.txt", b"shared-name>=1\ninner-only\n"),
            ("inner/main.py", b"import shared_name\nimport inner_only\n"),
        ]
    )
    return _zip_bytes(
        [
            (
                "wrapper/root-a/main.py",
                b"import os\nimport requests as http\nimport local_mod\n"
                b"import src_mod\nimport missing_name\nfrom . import sibling\n",
            ),
            ("wrapper/root-a/notebook.ipynb", _notebook()),
            ("wrapper/root-a/local_mod.py", b"VALUE = 1\n"),
            ("wrapper/root-a/src/src_mod/__init__.py", b"VALUE = 2\n"),
            (
                "wrapper/root-a/requirements.txt",
                b"requests>=2\nmissing-name[extra] == 1  # supported comment\n"
                b"unused-package\n-r other.txt\n",
            ),
            (
                "wrapper/root-a/nested/pyproject.toml",
                b"[project]\nname = 'nested'\ndependencies = ['nested-pkg']\n",
            ),
            (
                "wrapper/root-a/nested/main.py",
                b"import nested_pkg\nimport requests\n",
            ),
            ("wrapper/loose.py", b"import loose_local\n"),
            ("wrapper/loose_local.py", b"VALUE = 3\n"),
            ("wrapper/setup.py", b"from setuptools import setup\n"),
            ("wrapper/nested.zip", nested),
        ]
    )


def build_checker_inputs(root: Path) -> CheckerInputSet:
    """Build the complete small input matrix without third-party bytes."""

    root.mkdir(parents=True, exist_ok=True)
    direct = {
        "minimal_python": ("minimal.py", b"value = 1\n"),
        "absolute_paths": (
            "absolute-paths.py",
            b"import io\nimport os\nfrom pathlib import Path\n"
            b"open('/srv/data.csv')\nio.open(r'C:\\data.txt')\n"
            b"os.rename(src='~/old', dst=r'\\\\server\\share\\new')\n"
            b"Path(r'\\\\?\\C:\\device')\nPath('relative/data.csv')\n",
        ),
        "syntax_error": ("syntax-error.py", b"def broken(:\n"),
        "undecodable_python": ("undecodable.py", b"value = '\xff'\n"),
        "requirements": (
            "requirements-dev.txt",
            b"requests>=2\nlocal_pkg==1\n-r extra.txt\npackage; python_version > '3.10'\n",
        ),
        "pyproject": (
            "pyproject.toml",
            b"[project]\nname = 'fixture'\ndependencies = ['requests>=2', 'other_pkg']\n"
            b"[project.optional-dependencies]\ndev = ['pytest']\n",
        ),
        "valid_notebook": ("valid.ipynb", _notebook()),
        "unsupported_notebook": ("unsupported.ipynb", _notebook(language="R")),
        "malformed_notebook": ("malformed.ipynb", b'{"nbformat":4,"cells":['),
        "unsupported_version_notebook": (
            "unsupported-version.ipynb",
            _notebook(nbformat=3),
        ),
        "absent_language_notebook": (
            "absent-language.ipynb",
            _notebook(language=None),
        ),
        "conflicting_language_notebook": (
            "conflicting-language.ipynb",
            _notebook(language="python", language_info="R"),
        ),
        "no_code_notebook": (
            "no-code.ipynb",
            _notebook(
                cells=[
                    {
                        "cell_type": "markdown",
                        "metadata": {},
                        "source": "# No code\n",
                    }
                ]
            ),
        ),
        "cell_source_error_notebook": (
            "cell-source-error.ipynb",
            _notebook(
                cells=[
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": ["value = 1\n", 2],
                    }
                ]
            ),
        ),
        "magic_notebook": (
            "magic.ipynb",
            _notebook(
                cells=[
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": "  %matplotlib inline\n",
                    }
                ]
            ),
        ),
        "shell_notebook": (
            "shell.ipynb",
            _notebook(
                cells=[
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": "\t!python --version\n",
                    }
                ]
            ),
        ),
        "surrogate_notebook": (
            "surrogate.ipynb",
            _notebook(
                cells=[
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": "\ud800",
                    }
                ]
            ),
        ),
        "syntax_error_notebook": (
            "syntax-error.ipynb",
            _notebook(
                cells=[
                    {
                        "cell_type": "code",
                        "metadata": {},
                        "source": "def broken(:\n",
                    }
                ]
            ),
        ),
        "terminal_text": (
            "terminal.py",
            b"# [bold]unsafe[/bold]\x07" + "\u202e".encode() + b"\nvalue = 1\n",
        ),
        "unsupported_regular": ("artifact.bin", b"synthetic unsupported input\n"),
        "mutable_source": ("mutable.py", b"value = 1\n"),
    }
    paths: dict[str, Path] = {}
    for key, (name, body) in direct.items():
        path = root / name
        path.write_bytes(body)
        paths[key] = path

    paths["top_level_link"] = root / "input-link.py"
    paths["top_level_link"].symlink_to(paths["minimal_python"].name)

    nested = _nested_zip()
    hostile_entries: list[tuple[str, bytes] | tuple[str, bytes, int]] = [
        (
            "project/main.py",
            b"import requests\nimport local_pkg\nopen('/data/input')\n",
        ),
        ("project/local_pkg.py", b"LOCAL = True\n"),
        ("project/requirements.txt", b"requests>=2\nunused-package==1\n-r extra.txt\n"),
        ("project/pyproject.toml", b"[project]\ndependencies = ['requests>=2']\n"),
        ("project/notebook.ipynb", _notebook()),
        ("dup.py", b"first = 1\n"),
        ("dup.py", b"second = 2\n"),
        ("/absolute.py", b"x = 1\n"),
        ("../escape.py", b"x = 1\n"),
        (r"C:\root\drive.py", b"x = 1\n"),
        (r"\\server\share\unc.py", b"x = 1\n"),
        (r"\\?\C:\device.py", b"x = 1\n"),
        ("control\nname.py", b"x = 1\n"),
        (
            "bidi\u202ename.py",
            b"# [bold]x[/bold]\x07" + "\u202e".encode() + b"\nvalue = 1\n",
        ),
        ("link.py", b"project/main.py", stat.S_IFLNK | 0o777),
        ("pipe", b"", stat.S_IFIFO | 0o644),
        ("explicit-directory/", b"", stat.S_IFIFO | 0o644),
        ("environment.yml", b"dependencies:\n  - python\n"),
        ("one.zip", nested),
    ]
    hostile = _zip_bytes(hostile_entries)
    paths["hostile_zip"] = root / "hostile.zip"
    paths["hostile_zip"].write_bytes(hostile)

    basic = _zip_bytes([("main.py", b"value = 1\n")])
    variants = {
        "zip64": ("zip64.zip", _zip_bytes([("main.py", b"x = 1\n")], force_zip64=True)),
        "encrypted_zip": ("encrypted.zip", _set_encrypted(basic)),
        "unsupported_compression_zip": (
            "unsupported-compression.zip",
            _set_compression(basic, 99),
        ),
        "crc_corrupt_zip": ("crc-corrupt.zip", _corrupt_crc(basic)),
        "opaque_corrupt_zip": (
            "opaque-corrupt.zip",
            _corrupt_crc(
                _zip_bytes(
                    [
                        ("main.py", b"value = 1\n"),
                        ("payload.bin", b"opaque payload\n"),
                    ]
                ),
                occurrence=1,
            ),
        ),
        "central_directory_error_zip": (
            "central-directory-error.zip",
            _corrupt_central_directory(basic),
        ),
        "zip_named_python": ("zip-payload.py", basic),
        "docx_named_zip": ("document.docx", basic),
        "special_python_zip": (
            "special-python.zip",
            _zip_bytes([("blocked.py", b"", stat.S_IFIFO | 0o644)]),
        ),
        "dependency_zip": ("dependency-evidence.zip", _dependency_zip()),
    }
    for key, (name, body) in variants.items():
        path = root / name
        path.write_bytes(body)
        paths[key] = path

    return CheckerInputSet(paths=paths)


def mutate_source_same_size(path: Path) -> None:
    """Mutate the fixture in place while preserving inode and byte size."""

    before = path.stat()
    replacement = b"value = 2\n"
    if len(replacement) != before.st_size:
        raise ValueError("replacement must preserve the fixture size")
    with path.open("r+b") as handle:
        handle.write(replacement)
        handle.flush()
        os.fsync(handle.fileno())
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

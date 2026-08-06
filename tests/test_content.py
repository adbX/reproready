"""Tests for the targeted archive-bytes reader.

Builds zips / tar.gz / nested zips / a bare dir in tmp_path and checks that
``read_members`` returns exactly the requested members' bytes, capped, and that
the nested ``outer.zip/inner`` path form resolves.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from reproready.content import read_members


def _make_zip(path: Path, spec: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in spec.items():
            zf.writestr(name, body)


def _make_targz(path: Path, spec: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, body in spec.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))


def test_zip_direct_members(tmp_path: Path) -> None:
    p = tmp_path / "a.zip"
    _make_zip(p, {"main.py": b"x = 1\n", "README.md": b"# hi\n", "data.csv": b"a,b\n"})
    got = read_members(p, ".zip", {"main.py", "README.md"})
    assert got == {"main.py": b"x = 1\n", "README.md": b"# hi\n"}


def test_zip_missing_member_absent(tmp_path: Path) -> None:
    p = tmp_path / "a.zip"
    _make_zip(p, {"main.py": b"x = 1\n"})
    got = read_members(p, ".zip", {"main.py", "ghost.py"})
    assert set(got) == {"main.py"}


def test_member_cap(tmp_path: Path) -> None:
    p = tmp_path / "big.zip"
    _make_zip(p, {"big.bin": b"A" * 5000})
    got = read_members(p, ".zip", {"big.bin"}, cap=1000)
    assert len(got["big.bin"]) == 1000


def test_targz_members(tmp_path: Path) -> None:
    p = tmp_path / "a.tar.gz"
    _make_targz(p, {"src/model.py": b"m = 1\n", "setup.py": b"s = 2\n"})
    got = read_members(p, ".tar.gz", {"src/model.py"})
    assert got == {"src/model.py": b"m = 1\n"}


def test_nested_zip_member(tmp_path: Path) -> None:
    # Build inner zip, embed it in outer — matches reader's outer/inner path form.
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("main.py", b"print(1)\n")
        zf.writestr("pkg/util.py", b"u = 2\n")
    p = tmp_path / "outer.zip"
    with zipfile.ZipFile(p, "w") as outer:
        outer.writestr("code.zip", inner.getvalue())
        outer.writestr("top.txt", b"hi\n")
    got = read_members(
        p, ".zip", {"code.zip/main.py", "code.zip/pkg/util.py", "top.txt"}
    )
    assert got["code.zip/main.py"] == b"print(1)\n"
    assert got["code.zip/pkg/util.py"] == b"u = 2\n"
    assert got["top.txt"] == b"hi\n"


def test_repo_dir_members(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "model.py").write_bytes(b"m = 1\n")
    (repo / "requirements.txt").write_bytes(b"torch\n")
    got = read_members(repo, "", {"src/model.py", "requirements.txt"})
    assert got == {"src/model.py": b"m = 1\n", "requirements.txt": b"torch\n"}


def test_repo_dir_rejects_traversal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ok.py").write_bytes(b"1\n")
    got = read_members(repo, "", {"../secret", "/etc/passwd", "ok.py"})
    assert set(got) == {"ok.py"}


def test_empty_targets(tmp_path: Path) -> None:
    p = tmp_path / "a.zip"
    _make_zip(p, {"main.py": b"x\n"})
    assert read_members(p, ".zip", set()) == {}

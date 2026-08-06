"""Tests for :mod:`reproready.inventory`.

The invariant under test: the reader preserves everything a naive walker would
flatten or drop — deep entries survive, wrappers stay, duplicates stay, nested
archives expand, and broken innards surface as ``!ERROR`` rows.
"""

from __future__ import annotations

from pathlib import Path

from reproready.inventory import RawEntry, read_archive


def _paths(entries: list[RawEntry]) -> list[str]:
    return [e.path for e in entries]


def test_flat_zip(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "flat.zip", ".zip")
    assert result.archive_kind == "zip"
    assert result.error == ""
    assert set(_paths(result.entries)) == {"main.py", "README.md"}


def test_deep_zip_preserves_depth(fixtures_root: Path) -> None:
    """A naive depth cap would truncate; the raw reader must not."""
    result = read_archive(fixtures_root / "deep.zip", ".zip")
    assert result.error == ""
    paths = _paths(result.entries)
    assert "wrapper/src/pkg/module.py" in paths  # depth 4
    assert "wrapper/src/pkg/sub/leaf.py" in paths  # depth 5
    leaf = next(e for e in result.entries if e.path == "wrapper/src/pkg/sub/leaf.py")
    assert leaf.depth == 5
    assert not leaf.is_dir


def test_deep_zip_preserves_wrapper(fixtures_root: Path) -> None:
    """A single top-level wrapper must not be stripped."""
    result = read_archive(fixtures_root / "deep.zip", ".zip")
    paths = _paths(result.entries)
    # Every non-root entry sits under wrapper/ — wrapper prefix must persist.
    assert any(p.startswith("wrapper/") for p in paths)
    assert "wrapper/README.md" in paths


def test_duplicates_are_preserved(fixtures_root: Path) -> None:
    """Every entry is recorded, including repeated (path, is_dir) pairs."""
    result = read_archive(fixtures_root / "dupes.zip", ".zip")
    paths = [e.path for e in result.entries]
    assert paths.count("dup.txt") == 3


def test_empty_zip_has_zero_entries(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "empty.zip", ".zip")
    assert result.archive_kind == "zip"
    assert result.error == ""
    assert result.entries == []


def test_broken_zip_returns_error(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "broken.zip", ".zip")
    assert result.archive_kind == "zip"
    assert result.error
    assert result.entries == []


def test_pdf_single_entry(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "paper.pdf", ".pdf")
    assert result.archive_kind == "pdf"
    assert [e.path for e in result.entries] == ["paper.pdf"]
    assert result.entries[0].depth == 1


def test_missing_path(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "does-not-exist.zip", ".zip")
    assert result.archive_kind == "none"
    assert result.entries == []


def test_missing_pdf_path(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "nope.pdf", ".pdf")
    assert result.archive_kind == "pdf"
    assert result.entries == []


def test_targz_deep(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "deep.tar.gz", ".tar.gz")
    assert result.archive_kind == "targz"
    assert result.error == ""
    paths = _paths(result.entries)
    assert "wrapper/src/pkg/sub/leaf.py" in paths


def test_nested_zip_expansion(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "zip-in-zip.zip", ".zip")
    assert result.error == ""
    paths = _paths(result.entries)
    # The outer container entries are present verbatim (no rewrite).
    assert "code.zip" in paths
    assert "sibling.txt" in paths
    # Inner entries are prefixed with the outer path.
    assert "code.zip/main.py" in paths
    assert "code.zip/pkg/util.py" in paths


def test_nested_zip_depth_is_sum(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "zip-in-zip.zip", ".zip")
    by_path = {e.path: e for e in result.entries}
    # outer depth 1 + inner depth 1 = 2
    assert by_path["code.zip/main.py"].depth == 2
    # outer depth 1 + inner depth 2 = 3
    assert by_path["code.zip/pkg/util.py"].depth == 3


def test_broken_inner_zip_emits_error_row(fixtures_root: Path) -> None:
    result = read_archive(fixtures_root / "broken-inner.zip", ".zip")
    assert result.error == ""  # outer is fine
    paths = _paths(result.entries)
    assert "ok.txt" in paths  # sibling entries survive
    assert any(p.startswith("code.zip!ERROR") for p in paths)


def test_unknown_extension_sniffs_zip(fixtures_root: Path, tmp_path: Path) -> None:
    # Copy the flat zip to a file with no recognized extension.
    body = (fixtures_root / "flat.zip").read_bytes()
    target = tmp_path / "weird.bin"
    target.write_bytes(body)
    result = read_archive(target, "")
    assert result.archive_kind == "zip"
    assert {"main.py", "README.md"}.issubset(set(_paths(result.entries)))

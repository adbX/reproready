"""Executable expectations for the synthetic checker input matrix."""

from __future__ import annotations

import ast
import json
import stat
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from checker_input_fixtures import (
    CheckerInputSet,
    build_checker_inputs,
    mutate_source_same_size,
)

EXPECTED_CASES = {
    "absolute_paths",
    "absent_language_notebook",
    "cell_source_error_notebook",
    "central_directory_error_zip",
    "crc_corrupt_zip",
    "conflicting_language_notebook",
    "docx_named_zip",
    "encrypted_zip",
    "hostile_zip",
    "malformed_notebook",
    "minimal_python",
    "magic_notebook",
    "opaque_corrupt_zip",
    "mutable_source",
    "no_code_notebook",
    "pyproject",
    "requirements",
    "shell_notebook",
    "special_python_zip",
    "syntax_error",
    "syntax_error_notebook",
    "terminal_text",
    "surrogate_notebook",
    "top_level_link",
    "undecodable_python",
    "unsupported_compression_zip",
    "unsupported_notebook",
    "unsupported_version_notebook",
    "unsupported_regular",
    "valid_notebook",
    "zip64",
    "zip_named_python",
}


def test_case_set_is_complete_and_small(checker_inputs: CheckerInputSet) -> None:
    assert set(checker_inputs.paths) == EXPECTED_CASES
    assert sum(path.stat().st_size for path in checker_inputs.paths.values()) < 2**20
    assert checker_inputs.timeout_checkpoint == "after_source_read"
    assert checker_inputs.mutation_checkpoint == "after_snapshot_copy"
    assert checker_inputs.paths["top_level_link"].is_symlink()


def test_build_is_byte_deterministic(tmp_path: Path) -> None:
    first = build_checker_inputs(tmp_path / "first")
    second = build_checker_inputs(tmp_path / "second")
    assert {key: path.read_bytes() for key, path in first.paths.items()} == {
        key: path.read_bytes() for key, path in second.paths.items()
    }


def test_hostile_zip_preserves_structural_cases(
    checker_inputs: CheckerInputSet,
) -> None:
    with zipfile.ZipFile(checker_inputs.paths["hostile_zip"]) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        assert names.count("dup.py") == 2
        assert {
            "/absolute.py",
            "../escape.py",
            r"C:\root\drive.py",
            r"\\server\share\unc.py",
            r"\\?\C:\device.py",
            "control\nname.py",
            "bidi\u202ename.py",
        } <= set(names)
        assert stat.S_ISLNK(
            next(info for info in infos if info.filename == "link.py").external_attr
            >> 16
        )
        assert stat.S_ISFIFO(
            next(info for info in infos if info.filename == "pipe").external_attr >> 16
        )
        directory = next(
            info for info in infos if info.filename == "explicit-directory/"
        )
        assert directory.is_dir()
        assert stat.S_ISFIFO(directory.external_attr >> 16)


def test_nested_zip_reaches_one_level_beyond_contract(
    checker_inputs: CheckerInputSet,
) -> None:
    body = checker_inputs.paths["hostile_zip"].read_bytes()
    for name in ["one.zip", "two.zip", "three.zip", "four.zip"]:
        with zipfile.ZipFile(BytesIO(body)) as archive:
            assert name in archive.namelist()
            body = archive.read(name)
    with zipfile.ZipFile(BytesIO(body)) as archive:
        assert archive.namelist() == ["leaf.py"]


def test_zip_variants_expose_expected_reader_failures(
    checker_inputs: CheckerInputSet,
) -> None:
    with zipfile.ZipFile(checker_inputs.paths["encrypted_zip"]) as archive:
        assert archive.infolist()[0].flag_bits & 1
        with pytest.raises(RuntimeError):
            archive.read(archive.infolist()[0])

    with zipfile.ZipFile(
        checker_inputs.paths["unsupported_compression_zip"]
    ) as archive:
        assert archive.infolist()[0].compress_type == 99
        with pytest.raises(NotImplementedError):
            archive.read(archive.infolist()[0])

    with zipfile.ZipFile(checker_inputs.paths["crc_corrupt_zip"]) as archive:
        with pytest.raises(zipfile.BadZipFile):
            archive.read(archive.infolist()[0])
    with zipfile.ZipFile(checker_inputs.paths["opaque_corrupt_zip"]) as archive:
        assert archive.read(archive.infolist()[0]) == b"value = 1\n"
        with pytest.raises(zipfile.BadZipFile):
            archive.read(archive.infolist()[1])

    central_error = checker_inputs.paths["central_directory_error_zip"]
    assert zipfile.is_zipfile(central_error)
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(central_error)


def test_zip64_and_classification_precedence_inputs_are_real_zips(
    checker_inputs: CheckerInputSet,
) -> None:
    assert b"\x01\x00\x10\x00" in checker_inputs.paths["zip64"].read_bytes()
    assert zipfile.is_zipfile(checker_inputs.paths["zip_named_python"])
    assert checker_inputs.paths["zip_named_python"].suffix == ".py"
    assert zipfile.is_zipfile(checker_inputs.paths["docx_named_zip"])
    assert checker_inputs.paths["docx_named_zip"].suffix == ".docx"


def test_python_notebook_and_dependency_cases_are_explicit(
    checker_inputs: CheckerInputSet,
) -> None:
    path_source = checker_inputs.paths["absolute_paths"].read_text()
    tree = ast.parse(path_source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {
        "/srv/data.csv",
        r"C:\data.txt",
        "~/old",
        r"\\server\share\new",
        r"\\?\C:\device",
        "relative/data.csv",
    } <= literals
    with pytest.raises(SyntaxError):
        compile(checker_inputs.paths["syntax_error"].read_bytes(), "fixture", "exec")
    with pytest.raises(UnicodeDecodeError):
        checker_inputs.paths["undecodable_python"].read_text(encoding="utf-8")

    notebook = json.loads(checker_inputs.paths["valid_notebook"].read_text())
    assert isinstance(notebook["cells"][0]["source"], list)
    assert isinstance(notebook["cells"][1]["source"], str)
    assert "ignore/output-only" in notebook["cells"][0]["outputs"][0]["text"]
    assert (
        json.loads(checker_inputs.paths["unsupported_notebook"].read_text())[
            "metadata"
        ]["kernelspec"]["language"]
        == "R"
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(checker_inputs.paths["malformed_notebook"].read_text())

    assert b"-r extra.txt" in checker_inputs.paths["requirements"].read_bytes()
    assert b"optional-dependencies" in checker_inputs.paths["pyproject"].read_bytes()

    terminal_text = checker_inputs.paths["terminal_text"].read_text()
    assert "[bold]" in terminal_text
    assert "\x07" in terminal_text
    assert "\u202e" in terminal_text


def test_mutation_fixture_changes_identity_state_without_size_or_inode_change(
    tmp_path: Path,
) -> None:
    inputs = build_checker_inputs(tmp_path)
    source = inputs.paths["mutable_source"]
    before = source.stat()
    before_bytes = source.read_bytes()
    mutate_source_same_size(source)
    after = source.stat()
    assert source.read_bytes() != before_bytes
    assert after.st_size == before.st_size
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns != before.st_mtime_ns

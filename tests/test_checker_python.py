"""Behavioral contracts for bounded Python and notebook source indexing."""

from __future__ import annotations

import ast
import hashlib
import json
import warnings
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from reproready import checker_inventory
from reproready.checker import _check_document, _encode_check_document
from reproready.checker_intake import FIXED_LIMITS, SourceSnapshot
from reproready.checker_inventory import InspectionEngine
from reproready.checker_python import parse_python_source
from reproready.checker_report import _BoundedRecords, encode_report

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"


@pytest.fixture(scope="module")
def report_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _rule(report: dict[str, object], rule_id: str) -> dict[str, object]:
    return next(
        result for result in report["rule_results"] if result["rule_id"] == rule_id
    )


def _snapshot(path: Path) -> SourceSnapshot:
    body = path.read_bytes()
    return SourceSnapshot(
        path.name,
        len(body),
        hashlib.sha256(body).hexdigest(),
        path,
    )


def _write_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    with (
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive,
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore", UserWarning)
        for name, body in entries:
            archive.writestr(name, body)


def test_direct_python_has_exact_one_based_multiline_location(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    source = "é = 'café'\nresult = combine(\n    'left',\n    'right',\n)\n"
    path = tmp_path / "location.py"
    path.write_text(source, encoding="utf-8")

    report = _check_document(path)
    report_validator.validate(report)
    assert report["source_index"] == [
        {
            "source_id": "source:0",
            "member_id": None,
            "form": "python_file",
            "status": "inspected",
            "cell": None,
            "language": "python",
            "reason_code": None,
        }
    ]

    parsed = parse_python_source("source:0", None, None, source)
    call = parsed.tree.body[1].value
    assert isinstance(call, ast.Call)
    assert parsed.location(call) == {
        "source_id": "source:0",
        "member_id": None,
        "cell": None,
        "line": 2,
        "syntax": "combine(\n    'left',\n    'right',\n)",
    }
    repeated = parse_python_source("source:0", None, None, source)
    assert repeated.location(repeated.tree.body[1].value) == parsed.location(call)
    with pytest.raises(RuntimeError, match="positive one-based line"):
        parsed.location(ast.Pass())

    encoded = _encode_check_document(path)
    assert str(tmp_path).encode() not in encoded


def test_python_parser_does_not_emit_artifact_syntax_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SyntaxWarning)
        parsed = parse_python_source(
            "source:0",
            None,
            None,
            r"value = '\e'" + "\n",
        )

    assert caught == []
    assert isinstance(parsed.tree.body[0], ast.Assign)


def test_notebook_indexes_only_code_cells_at_physical_positions(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    path = checker_inputs.paths["valid_notebook"]
    report = _check_document(path)
    report_validator.validate(report)

    assert [
        (
            source["source_id"],
            source["form"],
            source["cell"],
            source["status"],
        )
        for source in report["source_index"]
    ] == [
        ("source:0", "notebook_document", None, "inspected"),
        ("source:1", "notebook_code_cell", 1, "inspected"),
        ("source:2", "notebook_code_cell", 3, "inspected"),
    ]
    assert [parser["name"] for parser in report["runtime"]["parsers"]] == [
        "ast",
        "json",
        "tokenize",
    ]

    notebook = json.loads(path.read_text(encoding="utf-8"))
    first_source = "".join(notebook["cells"][0]["source"])
    parsed = parse_python_source("source:1", None, 1, first_source)
    call = parsed.tree.body[1].value
    assert isinstance(call, ast.Call)
    assert parsed.location(call) == {
        "source_id": "source:1",
        "member_id": None,
        "cell": 1,
        "line": 2,
        "syntax": "open('/srv/input.csv')",
    }
    assert "ignore/output-only" not in parsed.source
    assert "ignore/raw-cell" not in parsed.source

    path_result = _rule(report, "python.absolute-path")
    assert path_result["status"] == "complete"
    assert path_result["skipped_inputs"] == []
    assert path_result["failed_inputs"] == []

    dependency_result = _rule(report, "python.dependencies")
    assert dependency_result["status"] == "complete"
    assert [
        (item["kind"], item["value"], item["cell"], item["line"])
        for item in dependency_result["evidence"]
    ] == [
        ("python_import", "requests", 1, 1),
        ("local_module", ".local", 3, 1),
    ]
    assert dependency_result["skipped_inputs"] == []
    assert dependency_result["failed_inputs"] == []


@pytest.mark.parametrize(
    ("fixture_name", "records", "parsers", "language"),
    [
        (
            "malformed_notebook",
            [("notebook_document", "error", "notebook_parse_error")],
            ["json"],
            None,
        ),
        (
            "unsupported_version_notebook",
            [
                (
                    "notebook_document",
                    "unsupported",
                    "unsupported_notebook_version",
                )
            ],
            ["json"],
            None,
        ),
        (
            "unsupported_notebook",
            [
                (
                    "notebook_document",
                    "unsupported",
                    "unsupported_notebook_language",
                )
            ],
            ["json"],
            "r",
        ),
        (
            "absent_language_notebook",
            [
                (
                    "notebook_document",
                    "unsupported",
                    "unsupported_notebook_language",
                )
            ],
            ["json"],
            None,
        ),
        (
            "conflicting_language_notebook",
            [
                (
                    "notebook_document",
                    "unsupported",
                    "unsupported_notebook_language",
                )
            ],
            ["json"],
            None,
        ),
        (
            "no_code_notebook",
            [("notebook_document", "inspected", None)],
            ["json"],
            "python",
        ),
        (
            "cell_source_error_notebook",
            [
                ("notebook_document", "inspected", None),
                (
                    "notebook_code_cell",
                    "error",
                    "notebook_cell_source_error",
                ),
            ],
            ["json"],
            "python",
        ),
        (
            "magic_notebook",
            [
                ("notebook_document", "inspected", None),
                (
                    "notebook_code_cell",
                    "unsupported",
                    "unsupported_notebook_syntax",
                ),
            ],
            ["json", "tokenize"],
            "python",
        ),
        (
            "shell_notebook",
            [
                ("notebook_document", "inspected", None),
                (
                    "notebook_code_cell",
                    "unsupported",
                    "unsupported_notebook_syntax",
                ),
            ],
            ["json", "shlex", "tokenize"],
            "python",
        ),
        (
            "surrogate_notebook",
            [
                ("notebook_document", "inspected", None),
                (
                    "notebook_code_cell",
                    "error",
                    "notebook_cell_source_error",
                ),
            ],
            ["json"],
            "python",
        ),
        (
            "syntax_error_notebook",
            [
                ("notebook_document", "inspected", None),
                ("notebook_code_cell", "error", "syntax_error"),
            ],
            ["ast", "json", "tokenize"],
            "python",
        ),
    ],
)
def test_notebook_outcomes_and_parser_metadata_are_distinct(
    checker_inputs,
    report_validator: Draft202012Validator,
    fixture_name: str,
    records: list[tuple[str, str, str | None]],
    parsers: list[str],
    language: str | None,
) -> None:
    report = _check_document(checker_inputs.paths[fixture_name])
    report_validator.validate(report)

    assert [
        (source["form"], source["status"], source["reason_code"])
        for source in report["source_index"]
    ] == records
    assert report["source_index"][0]["language"] == language
    assert [parser["name"] for parser in report["runtime"]["parsers"]] == parsers


def test_unsupported_language_is_bounded_without_partial_python_coverage(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    payload = {
        "cells": [{"cell_type": "code", "source": "value = 1\n"}],
        "metadata": {"kernelspec": {"language": "x" * 129}},
        "nbformat": 4,
    }
    path = tmp_path / "long-language.ipynb"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = _check_document(path)
    report_validator.validate(report)
    assert report["source_index"][0]["language"] is None
    for rule_id in ("python.absolute-path", "python.dependencies"):
        result = _rule(report, rule_id)
        assert result["status"] == "not_applicable"
        assert result["skipped_inputs"] == []
        assert result["failed_inputs"] == []


def test_zip_source_order_follows_global_member_preorder_without_path_exclusions(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    nested_bytes = BytesIO()
    with (
        zipfile.ZipFile(nested_bytes, "w", zipfile.ZIP_DEFLATED) as nested,
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore", UserWarning)
        nested.writestr("dup.py", "first = 1\n")
        nested.writestr("dup.py", "second = 2\n")

    path = tmp_path / "ordered.zip"
    _write_zip(
        path,
        [
            ("generated/_vendor.py", b"vendor = 1\n"),
            ("nested.zip", nested_bytes.getvalue()),
            ("_AppleDouble/main.py", b"metadata = 1\n"),
        ],
    )

    report = _check_document(path)
    report_validator.validate(report)
    assert [
        (source["source_id"], source["member_id"], source["status"])
        for source in report["source_index"]
    ] == [
        ("source:0", "member:0", "inspected"),
        ("source:1", "member:2", "inspected"),
        ("source:2", "member:3", "inspected"),
        ("source:3", "member:4", "inspected"),
    ]
    assert [
        member["duplicate_ordinal"]
        for member in report["inventory"]["members"]
        if member["name"] == "dup.py"
    ] == [1, 2]


@pytest.mark.parametrize(
    ("fixture_name", "member_id", "status", "reason_code", "coverage_kind"),
    [
        ("hostile_zip", "member:14", "skipped", "symlink_entry", "skipped_inputs"),
        (
            "special_python_zip",
            "member:0",
            "skipped",
            "special_entry",
            "skipped_inputs",
        ),
        ("encrypted_zip", "member:0", "skipped", "encrypted_entry", "skipped_inputs"),
        (
            "unsupported_compression_zip",
            "member:0",
            "skipped",
            "unsupported_compression",
            "skipped_inputs",
        ),
        ("crc_corrupt_zip", "member:0", "error", "integrity_error", "failed_inputs"),
    ],
)
def test_source_like_member_read_blockers_remain_indexed(
    checker_inputs,
    report_validator: Draft202012Validator,
    fixture_name: str,
    member_id: str,
    status: str,
    reason_code: str,
    coverage_kind: str,
) -> None:
    report = _check_document(checker_inputs.paths[fixture_name])
    report_validator.validate(report)
    source = next(
        source for source in report["source_index"] if source["member_id"] == member_id
    )
    assert (source["status"], source["reason_code"]) == (status, reason_code)

    for rule_id in ("python.absolute-path", "python.dependencies"):
        result = _rule(report, rule_id)
        assert any(
            item["source_id"] == source["source_id"]
            and item["reason_code"] == reason_code
            for item in result[coverage_kind]
        )


def test_cache_failure_is_an_explicit_failed_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_validator: Draft202012Validator,
) -> None:
    path = tmp_path / "cache-failure.zip"
    _write_zip(path, [("main.py", b"value = 1\n")])
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    real_open = checker_inventory.os.open

    def fail_cache_open(path_value, *args, **kwargs):
        if str(path_value).endswith(".partial"):
            raise OSError("synthetic cache failure")
        return real_open(path_value, *args, **kwargs)

    monkeypatch.setattr(checker_inventory.os, "open", fail_cache_open)
    engine = InspectionEngine(_snapshot(path), lambda _checkpoint: None, cache_root)
    report = engine.inspect()
    report_validator.validate(report)

    assert report["source_index"][0]["status"] == "error"
    assert report["source_index"][0]["reason_code"] == "checker_cache_error"
    assert [parser["name"] for parser in report["runtime"]["parsers"]] == ["zipfile"]
    for rule_id in ("python.absolute-path", "python.dependencies"):
        result = _rule(report, rule_id)
        assert [item["reason_code"] for item in result["failed_inputs"]] == [
            "checker_cache_error"
        ]


def test_parser_limit_skips_before_ast_is_invoked(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "too-large.py"
    size = FIXED_LIMITS["max_python_source_bytes"] + 1
    with path.open("wb") as handle:
        handle.truncate(size)
    snapshot = SourceSnapshot(path.name, size, "a" * 64, path)
    engine = InspectionEngine(snapshot, lambda _checkpoint: None, tmp_path / "cache")

    report = engine.inspect()
    report_validator.validate(report)
    assert report["source_index"][0]["status"] == "skipped"
    assert report["source_index"][0]["reason_code"] == "max_python_source_bytes"
    assert report["runtime"]["parsers"] == []
    assert report["limits"]["reached"] == ["max_python_source_bytes"]


def test_malformed_notebook_blocks_coverage_but_unsupported_notebook_does_not(
    checker_inputs,
) -> None:
    malformed = _check_document(checker_inputs.paths["malformed_notebook"])
    unsupported = _check_document(checker_inputs.paths["unsupported_version_notebook"])

    for rule_id in ("python.absolute-path", "python.dependencies"):
        malformed_result = _rule(malformed, rule_id)
        assert malformed_result["status"] == "partial"
        assert [item["reason_code"] for item in malformed_result["failed_inputs"]] == [
            "notebook_parse_error"
        ]
        unsupported_result = _rule(unsupported, rule_id)
        assert unsupported_result["status"] == "not_applicable"
        assert unsupported_result["skipped_inputs"] == []
        assert unsupported_result["failed_inputs"] == []


def test_report_limit_keeps_dense_source_prefix_and_stops_later_parses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_validator: Draft202012Validator,
) -> None:
    path = tmp_path / "report-limit.zip"
    entries = [
        (f"{'x' * 100}-{index:02d}.py", f"value = {index}\n".encode())
        for index in range(20)
    ]
    _write_zip(path, entries)
    cache_root = tmp_path / "report-limit-cache"
    cache_root.mkdir()
    engine = InspectionEngine(_snapshot(path), lambda _checkpoint: None, cache_root)
    engine.records = _BoundedRecords(
        max_report_bytes=16 * 1024,
        max_observations=FIXED_LIMITS["max_observations"],
        reserve_bytes=8 * 1024,
    )
    parse_calls = 0
    real_parse = checker_inventory.parse_python_source

    def counting_parse(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(checker_inventory, "parse_python_source", counting_parse)
    report = engine.inspect()
    encoded = encode_report(report)
    report_validator.validate(report)

    assert report["limits"]["reached"] == ["max_report_bytes"]
    assert [source["source_id"] for source in report["source_index"]] == [
        f"source:{index}" for index in range(len(report["source_index"]))
    ]
    assert 0 < len(report["source_index"]) < len(entries)
    assert parse_calls == len(report["source_index"]) + 1
    assert len(encoded) <= engine.records.max_report_bytes

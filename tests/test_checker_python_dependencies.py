"""Behavioral contracts for bounded Python dependency evidence."""

from __future__ import annotations

import hashlib
import json
import stat
import warnings
import zipfile
from importlib.metadata import version
from io import StringIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from rich.console import Console

from reproready.checker import _check_document, _encode_check_document
from reproready.checker_intake import FIXED_LIMITS, SourceSnapshot
from reproready.checker_inventory import InspectionEngine
from reproready.checker_render import render_report
from reproready.checker_report import _BoundedRecords, encode_report

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"


@pytest.fixture(scope="module")
def report_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _rule(report: dict[str, object]) -> dict[str, object]:
    return next(
        result
        for result in report["rule_results"]
        if result["rule_id"] == "python.dependencies"
    )


def _snapshot(path: Path) -> SourceSnapshot:
    body = path.read_bytes()
    return SourceSnapshot(
        path.name,
        len(body),
        hashlib.sha256(body).hexdigest(),
        path,
    )


def _write_zip(
    path: Path,
    entries: list[tuple[str, bytes] | tuple[str, bytes, int]],
) -> None:
    with (
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive,
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore", UserWarning)
        for entry in entries:
            name, body, *mode = entry
            info = zipfile.ZipInfo(name, (2020, 1, 2, 3, 4, 6))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (mode[0] if mode else stat.S_IFREG | 0o644) << 16
            archive.writestr(info, body)


def _notebook(cells: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "cells": cells,
            "metadata": {"kernelspec": {"language": "python", "name": "python"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _render(report: dict[str, object]) -> str:
    output = StringIO()
    console = Console(
        file=output,
        color_system=None,
        force_terminal=False,
        no_color=True,
        width=200,
    )
    render_report(report, console)
    return output.getvalue()


def _assert_dense_and_valid_references(report: dict[str, object]) -> None:
    evidence = [
        item for result in report["rule_results"] for item in result["evidence"]
    ]
    observations = [
        item for result in report["rule_results"] for item in result["observations"]
    ]
    assert [item["evidence_id"] for item in evidence] == [
        f"evidence:{index}" for index in range(len(evidence))
    ]
    assert [item["observation_id"] for item in observations] == [
        f"observation:{index}" for index in range(len(observations))
    ]
    evidence_ids = {item["evidence_id"] for item in evidence}
    source_ids = {item["source_id"] for item in report["source_index"]}
    member_ids = {item["member_id"] for item in report["inventory"]["members"]}
    for item in evidence:
        assert set(item["related_evidence_ids"]) <= evidence_ids
        assert all(
            int(related.split(":", 1)[1]) < int(item["evidence_id"].split(":", 1)[1])
            for related in item["related_evidence_ids"]
        )
        assert item["source_id"] is None or item["source_id"] in source_ids
        assert item["member_id"] is None or item["member_id"] in member_ids
    for item in observations:
        assert set(item["evidence_ids"]) <= evidence_ids
        assert item["source_id"] is None or item["source_id"] in source_ids
        assert item["member_id"] is None or item["member_id"] in member_ids


def test_imports_use_the_existing_ast_and_exact_source_locations(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "imports.py"
    path.write_text(
        "import Alpha.beta as alias, gamma\n"
        "from delta.mod import value as renamed\n"
        "from .pkg import local\n"
        "from .. import sibling, other\n"
        "if False:\n"
        "    import unreachable\n"
        "def load():\n"
        "    import function_local\n"
        "try:\n"
        "    import optional_import\n"
        "except ImportError:\n"
        "    pass\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import typing_only\n"
        "import importlib\n"
        "importlib.import_module('dynamic_name')\n"
        "__import__('hidden_name')\n",
        encoding="utf-8",
    )

    report = _check_document(path)
    report_validator.validate(report)
    result = _rule(report)
    imports = [item for item in result["evidence"] if item["kind"] == "python_import"]
    assert [(item["value"], item["line"]) for item in imports] == [
        ("Alpha", 1),
        ("gamma", 1),
        ("delta", 2),
        ("unreachable", 6),
        ("function_local", 8),
        ("optional_import", 10),
        ("typing", 13),
        ("typing_only", 15),
        ("importlib", 16),
    ]
    assert all(item["source_id"] == "source:0" for item in imports)
    relative = [
        item["value"] for item in result["evidence"] if item["kind"] == "local_module"
    ]
    assert relative == [".pkg", "..other", "..sibling"]
    assert not {"dynamic_name", "hidden_name"} & {
        item["value"] for item in result["evidence"]
    }
    reviews = {
        (item["condition_code"], item["line"], item["snippet"])
        for item in result["observations"]
    }
    assert (
        "import_without_exact_declaration",
        1,
        "import Alpha.beta as alias, gamma",
    ) in reviews
    assert (
        "import_without_exact_declaration",
        2,
        "from delta.mod import value as renamed",
    ) in reviews
    assert not any(item["line"] in {13, 16} for item in result["observations"])


def test_notebook_imports_ignore_non_code_surfaces(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "imports.ipynb"
    path.write_bytes(
        _notebook(
            [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [
                        {"output_type": "stream", "text": "import output_only"}
                    ],
                    "source": "import code_only\ntext = 'import string_only'\n",
                },
                {
                    "cell_type": "markdown",
                    "metadata": {},
                    "source": "import markdown_only",
                },
                {
                    "cell_type": "raw",
                    "metadata": {},
                    "source": "import raw_only",
                },
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["from . import local\n"],
                },
            ]
        )
    )

    report = _check_document(path)
    report_validator.validate(report)
    evidence = _rule(report)["evidence"]
    assert [
        (item["kind"], item["value"], item["cell"], item["line"]) for item in evidence
    ] == [
        ("python_import", "code_only", 1, 1),
        ("local_module", ".local", 4, 1),
    ]


def test_requirements_reduction_keeps_supported_siblings_and_exact_text(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "requirements-dev.txt"
    path.write_text(
        "\n# ignored\n"
        "Name_Pkg[fast] ~= 1.2  # retained only in review syntax\n"
        "other.pkg===2\n"
        "-r included.txt\n"
        "--index-url https://example.invalid/simple\n"
        "-e ./editable\n"
        "../local-project\n"
        "https://example.invalid/archive.whl\n"
        "git+https://example.invalid/repo.git\n"
        "continued \\\n"
        "marked; python_version > '3.10'\n"
        "direct @ https://example.invalid/direct.whl\n"
        "not a requirement ???\n" + "x" * 4097 + "\n",
        encoding="utf-8",
    )

    report = _check_document(path)
    report_validator.validate(report)
    result = _rule(report)
    declarations = [
        item for item in result["evidence"] if item["kind"] == "dependency_declaration"
    ]
    assert [(item["value"], item["line"]) for item in declarations] == [
        ("Name_Pkg[fast] ~= 1.2", 3),
        ("other.pkg===2", 4),
    ]
    assert result["status"] == "partial"
    assert {item["reason_code"] for item in result["skipped_inputs"]} == {
        "unsupported_dependency_syntax"
    }
    assert [item["snippet"] for item in result["observations"]] == [
        "Name_Pkg[fast] ~= 1.2  # retained only in review syntax",
        "other.pkg===2",
    ]
    parsers = {item["name"]: item["version"] for item in report["runtime"]["parsers"]}
    assert parsers == {"packaging": version("packaging")}


def test_pyproject_reads_only_pep621_and_preserves_null_lines(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "[project]\n"
        "name = 'fixture'\n"
        "dependencies = [\n"
        "  'Core_Pkg>=1',\n"
        "  \"marked; python_version > '3.10'\",\n"
        "  'direct @ https://example.invalid/pkg.whl',\n"
        "]\n"
        "[project.optional-dependencies]\n"
        "dev = ['pytest']\n"
        "[dependency-groups]\n"
        "docs = ['sphinx']\n"
        "[tool.poetry.dependencies]\n"
        "python = '^3.10'\n"
        "[tool.pdm.dependencies]\n"
        "extra = '*'\n",
        encoding="utf-8",
    )

    report = _check_document(path)
    report_validator.validate(report)
    result = _rule(report)
    assert [
        (item["value"], item["line"], item["cell"])
        for item in result["evidence"]
        if item["kind"] == "dependency_declaration"
    ] == [("Core_Pkg>=1", None, None)]
    assert result["status"] == "partial"
    assert {item["reason_code"] for item in result["skipped_inputs"]} == {
        "unsupported_dependency_syntax",
        "unsupported_dependency_table",
    }
    assert [item["snippet"] for item in result["observations"]] == ["Core_Pkg>=1"]
    parsers = {item["name"]: item["version"] for item in report["runtime"]["parsers"]}
    assert parsers == {"packaging": version("packaging"), "tomllib": parsers["tomllib"]}

    malformed = tmp_path / "malformed" / "pyproject.toml"
    malformed.parent.mkdir()
    malformed.write_text("[project\ndependencies = []\n", encoding="utf-8")
    malformed_report = _check_document(malformed)
    report_validator.validate(malformed_report)
    malformed_result = _rule(malformed_report)
    assert [item["reason_code"] for item in malformed_result["failed_inputs"]] == [
        "dependency_parse_error"
    ]

    invalid = tmp_path / "pyproject.toml"
    invalid.write_text("[project]\ndependencies = 'not-an-array'\n", encoding="utf-8")
    invalid_report = _check_document(invalid)
    report_validator.validate(invalid_report)
    invalid_result = _rule(invalid_report)
    assert invalid_result["status"] == "partial"
    assert [item["reason_code"] for item in invalid_result["failed_inputs"]] == [
        "dependency_parse_error"
    ]


def test_normalized_matches_roots_local_modules_and_containers_are_isolated(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    report = _check_document(checker_inputs.paths["dependency_zip"])
    report_validator.validate(report)
    result = _rule(report)
    assert result["status"] == "partial"
    assert {item["kind"] for item in result["evidence"]} == {
        "python_import",
        "dependency_declaration",
        "standard_library",
        "local_module",
    }
    assert any(
        source["form"] == "unsupported_source"
        and source["status"] == "unsupported"
        and source["reason_code"] == "unsupported_dependency_form"
        for source in report["source_index"]
    )

    reviews = [
        (item["condition_code"], item["snippet"]) for item in result["observations"]
    ]
    assert ("import_without_exact_declaration", "import requests") in reviews
    assert ("declaration_without_exact_import", "unused-package") in reviews
    assert not any("missing_name" in str(snippet) for _, snippet in reviews)
    assert not any("nested_pkg" in str(snippet) for _, snippet in reviews)
    assert not any("inner_only" in str(snippet) for _, snippet in reviews)
    assert not any("loose_local" in str(snippet) for _, snippet in reviews)
    assert any(item["value"] == "shared-name>=1" for item in result["evidence"])

    local_values = {
        item["value"] for item in result["evidence"] if item["kind"] == "local_module"
    }
    assert {
        ".sibling",
        "wrapper/root-a/local_mod.py",
        "wrapper/root-a/src/src_mod/__init__.py",
        "wrapper/loose_local.py",
    } <= local_values
    stdlib = [item for item in result["evidence"] if item["kind"] == "standard_library"]
    assert [(item["value"], item["line"]) for item in stdlib] == [("os", 1)]
    _assert_dense_and_valid_references(report)
    assert any(item["related_evidence_ids"] for item in result["evidence"])


def test_local_module_rules_are_exact_and_do_not_cross_nested_roots(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "local-boundaries.zip"
    _write_zip(
        path,
        [
            ("root/requirements.txt", b""),
            (
                "root/main.py",
                b"import LocalCase\nimport namespace_only\nimport linked\nimport nested_local\n",
            ),
            ("root/localcase.py", b"VALUE = 1\n"),
            ("root/namespace_only/module.py", b"VALUE = 2\n"),
            ("root/linked.py", b"target.py", stat.S_IFLNK | 0o777),
            ("root/nested/requirements.txt", b""),
            ("root/nested/nested_local.py", b"VALUE = 3\n"),
        ],
    )

    report = _check_document(path)
    report_validator.validate(report)
    result = _rule(report)
    assert not [item for item in result["evidence"] if item["kind"] == "local_module"]
    assert [
        item["snippet"]
        for item in result["observations"]
        if item["condition_code"] == "import_without_exact_declaration"
    ] == [
        "import LocalCase",
        "import namespace_only",
        "import linked",
        "import nested_local",
    ]


def test_exact_matches_are_normalized_without_distribution_aliases(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "normalization.zip"
    _write_zip(
        path,
        [
            (
                "project/main.py",
                b"import Mixed_Name\nimport scikit_learn\nimport yaml\n",
            ),
            (
                "project/requirements.txt",
                b"mixed.name>=1\nscikit-learn\nPyYAML\n",
            ),
        ],
    )

    report = _check_document(path)
    report_validator.validate(report)
    result = _rule(report)
    reviews = [
        (item["condition_code"], item["snippet"]) for item in result["observations"]
    ]
    assert not any("Mixed_Name" in str(snippet) for _, snippet in reviews)
    assert not any("scikit" in str(snippet) for _, snippet in reviews)
    assert ("import_without_exact_declaration", "import yaml") in reviews
    assert ("declaration_without_exact_import", "PyYAML") in reviews


def test_all_explicit_dependency_forms_are_indexed_without_content_reads(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "unsupported-forms.zip"
    unsupported_names = [
        "setup.py",
        "Pipfile",
        "Pipfile.lock",
        "other.lock",
        "requirements-dev.in",
        "constraints.txt",
        "constraints-dev.in",
        "environment.yml",
        "environment-dev.yaml",
        "conda.yml",
        "conda-cpu.yaml",
        "Dockerfile",
        "Dockerfile.gpu",
    ]
    _write_zip(
        path,
        [(name, b"must not be parsed") for name in unsupported_names]
        + [
            (
                "pyproject.toml",
                b"[tool.poetry.dependencies]\npython = '^3.10'\n",
            ),
            ("unrelated.txt", b"ignored\n"),
        ],
    )

    report = _check_document(path)
    report_validator.validate(report)
    unsupported_sources = [
        source
        for source in report["source_index"]
        if source["form"] == "unsupported_source"
    ]
    assert len(unsupported_sources) == len(unsupported_names)
    assert all(source["status"] == "unsupported" for source in unsupported_sources)
    source_member_ids = {source["member_id"] for source in unsupported_sources}
    assert {
        member["name"]
        for member in report["inventory"]["members"]
        if member["member_id"] in source_member_ids
        and member["read_status"] == "not_read"
    } == set(unsupported_names)
    pyproject_sources = [
        source for source in report["source_index"] if source["form"] == "pyproject"
    ]
    assert len(pyproject_sources) == 1
    result = _rule(report)
    assert result["status"] == "partial"
    assert [item["reason_code"] for item in result["skipped_inputs"]].count(
        "unsupported_dependency_form"
    ) == len(unsupported_names)
    assert "unsupported_dependency_table" in {
        item["reason_code"] for item in result["skipped_inputs"]
    }


def test_all_four_local_layouts_direct_self_match_and_stdlib_case_are_exact(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "local-layouts.zip"
    _write_zip(
        path,
        [
            ("root/requirements.txt", b""),
            (
                "root/main.py",
                b"import flat_mod\nimport package_mod\nimport src_flat\n"
                b"import src_package\nimport namespace_mod\nimport Json\nimport json\n",
            ),
            ("root/flat_mod.py", b"VALUE = 1\n"),
            ("root/package_mod/__init__.py", b"VALUE = 2\n"),
            ("root/src/src_flat.py", b"VALUE = 3\n"),
            ("root/src/src_package/__init__.py", b"VALUE = 4\n"),
            ("root/namespace_mod/child.py", b"VALUE = 5\n"),
        ],
    )

    report = _check_document(path)
    report_validator.validate(report)
    result = _rule(report)
    assert {
        item["value"] for item in result["evidence"] if item["kind"] == "local_module"
    } == {
        "root/flat_mod.py",
        "root/package_mod/__init__.py",
        "root/src/src_flat.py",
        "root/src/src_package/__init__.py",
    }
    assert [
        item["value"]
        for item in result["evidence"]
        if item["kind"] == "standard_library"
    ] == ["json"]
    review_snippets = {item["snippet"] for item in result["observations"]}
    assert {"import namespace_mod", "import Json"} <= review_snippets
    assert "import json" not in review_snippets

    direct = tmp_path / "own_module.py"
    direct.write_text("import own_module\n", encoding="utf-8")
    direct_report = _check_document(direct)
    report_validator.validate(direct_report)
    direct_result = _rule(direct_report)
    assert [
        (
            item["value"],
            item["member_id"],
            item["source_id"],
            item["line"],
        )
        for item in direct_result["evidence"]
        if item["kind"] == "local_module"
    ] == [("own_module.py", None, "source:0", None)]
    assert direct_result["observations"] == []


def test_same_root_declarations_combine_and_wrapper_fallback_is_all_or_nothing(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    combined = tmp_path / "combined-root.zip"
    _write_zip(
        combined,
        [
            ("wrapper/requirements.txt", b"first-name\n"),
            ("wrapper/requirements-dev.txt", b"second_name\n"),
            (
                "wrapper/pyproject.toml",
                b"[project]\ndependencies = ['third.name']\n",
            ),
            (
                "wrapper/main.py",
                b"import first_name\nimport second_name\nimport third_name\n",
            ),
        ],
    )
    report = _check_document(combined)
    report_validator.validate(report)
    result = _rule(report)
    assert result["status"] == "complete"
    assert result["observations"] == []
    assert [
        item["value"]
        for item in result["evidence"]
        if item["kind"] == "dependency_declaration"
    ] == ["first-name", "second_name", "third.name"]

    split_wrapper = tmp_path / "split-wrapper.zip"
    _write_zip(
        split_wrapper,
        [
            ("wrapper/main.py", b"import helper\n"),
            ("wrapper/helper.py", b"VALUE = 1\n"),
            ("outside.txt", b"prevents wrapper fallback\n"),
        ],
    )
    split_report = _check_document(split_wrapper)
    report_validator.validate(split_report)
    split_result = _rule(split_report)
    assert not [
        item for item in split_result["evidence"] if item["kind"] == "local_module"
    ]
    assert [item["snippet"] for item in split_result["observations"]] == [
        "import helper"
    ]


def test_path_owned_report_cap_leaves_dependency_limit_coverage(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "path-report-owned.py"
    source = "import dependency_name\n" + "\n".join(
        f"open('/{'x' * 190}-{index:03d}')" for index in range(100)
    )
    path.write_text(source, encoding="utf-8")
    engine = InspectionEngine(
        _snapshot(path), lambda _checkpoint: None, tmp_path / "path-report-cache"
    )
    engine.records = _BoundedRecords(
        max_report_bytes=24 * 1024,
        max_observations=FIXED_LIMITS["max_observations"],
        reserve_bytes=8 * 1024,
    )

    report = engine.inspect()
    report_validator.validate(report)
    path_result = report["rule_results"][1]
    result = _rule(report)
    assert path_result["observations"][-1]["condition_code"] == "resource_limit_reached"
    assert result["evidence"] == []
    assert result["observations"] == []
    assert result["status"] == "partial"
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "report_size_limit"
    ]
    _assert_dense_and_valid_references(report)


def test_dependency_observation_cap_coexists_with_path_priority(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "path-owned.py"
    path.write_text(
        "import first_missing\nimport second_missing\n"
        "open('/one')\nopen('/two')\nopen('/three')\n",
        encoding="utf-8",
    )
    engine = InspectionEngine(
        _snapshot(path), lambda _checkpoint: None, tmp_path / "cache"
    )
    engine.records = _BoundedRecords(
        max_report_bytes=FIXED_LIMITS["max_report_bytes"],
        max_observations=3,
    )

    report = engine.inspect()
    report_validator.validate(report)
    path_result = report["rule_results"][1]
    result = _rule(report)
    assert [item["condition_code"] for item in path_result["observations"]] == [
        "posix_absolute_path",
        "posix_absolute_path",
        "resource_limit_reached",
    ]
    assert [
        item["value"] for item in result["evidence"] if item["kind"] == "python_import"
    ] == [
        "first_missing",
        "second_missing",
    ]
    assert result["observations"] == []
    assert result["status"] == "partial"
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "observation_limit"
    ]
    _assert_dense_and_valid_references(report)


def test_dependency_owns_terminal_observation_and_report_prefix(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    observation_path = tmp_path / "dependency-owned.py"
    observation_path.write_text(
        "\n".join(f"import missing_{index}" for index in range(5)),
        encoding="utf-8",
    )
    observation_engine = InspectionEngine(
        _snapshot(observation_path),
        lambda _checkpoint: None,
        tmp_path / "observation-cache",
    )
    observation_engine.records = _BoundedRecords(
        max_report_bytes=FIXED_LIMITS["max_report_bytes"],
        max_observations=3,
    )
    observation_report = observation_engine.inspect()
    report_validator.validate(observation_report)
    observation_result = _rule(observation_report)
    assert [item["condition_code"] for item in observation_result["observations"]] == [
        "import_without_exact_declaration",
        "import_without_exact_declaration",
        "resource_limit_reached",
    ]
    assert observation_result["status"] == "partial"

    report_path = tmp_path / "report-owned.py"
    report_path.write_text(
        "\n".join(f"import dependency_{index:03d}_{'x' * 180}" for index in range(100)),
        encoding="utf-8",
    )
    report_engine = InspectionEngine(
        _snapshot(report_path), lambda _checkpoint: None, tmp_path / "report-cache"
    )
    report_engine.records = _BoundedRecords(
        max_report_bytes=24 * 1024,
        max_observations=FIXED_LIMITS["max_observations"],
        reserve_bytes=8 * 1024,
    )
    report = report_engine.inspect()
    report_validator.validate(report)
    assert len(encode_report(report)) <= report_engine.records.max_report_bytes
    assert report["limits"]["reached"] == ["max_report_bytes"]
    result = _rule(report)
    assert result["status"] == "partial"
    assert result["observations"][-1]["condition_code"] == "resource_limit_reached"
    _assert_dense_and_valid_references(report)


def test_dependency_renderer_escapes_values_and_prints_relationships(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("safe_pkg  # [bold]unsafe[/bold]\u0007\u202e\n", encoding="utf-8")

    report = _check_document(path)
    report_validator.validate(report)
    output = _render(report)
    assert "Dependency declarations without exact imports" in output
    assert "1 human-review observation" in output
    assert "requirements.txt · line 1" in output
    assert "[bold]unsafe[/bold]\\u0007\\u202e" in output
    assert "evidence:0" not in output
    assert "\u0007" not in output
    assert "\u202e" not in output


def test_dependency_renderer_shows_coordinates_relationships_and_coverage(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "render-dependencies.zip"
    _write_zip(
        path,
        [
            (
                "project/main.py",
                b"import matched_name\nimport missing_name\n",
            ),
            (
                "project/requirements.txt",
                b"matched-name\nunused-name\n",
            ),
            ("project/setup.py", b"ignored\n"),
            ("project/broken.py", b"def broken(:\n"),
        ],
    )

    report = _check_document(path)
    report_validator.validate(report)
    output = _render(report)
    assert output.index("Inspection limitations") < output.index("Overview")
    assert "Python dependencies (python.dependencies): partial" in output
    assert "Imports without exact dependency declarations" in output
    assert "Dependency declarations without exact imports" in output
    assert "project/main.py · line 2" in output
    assert "project/requirements.txt · line 2" in output
    assert "project/setup.py" in output
    assert "project/broken.py" in output
    assert "member:" not in output
    assert "source:" not in output


def test_dependency_results_are_runtime_deterministic(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    path = checker_inputs.paths["dependency_zip"]
    first = _check_document(path)
    second = _check_document(path)
    report_validator.validate(first)
    assert first == second
    assert _encode_check_document(path) == _encode_check_document(path)
    _assert_dense_and_valid_references(first)

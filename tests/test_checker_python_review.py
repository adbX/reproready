"""Behavioral contracts for the retained Python review rules."""

from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from reproready.checker import _check_document
from reproready.checker_intake import FIXED_LIMITS, SourceSnapshot
from reproready.checker_inventory import InspectionEngine
from reproready.checker_report import _BoundedRecords

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "src/reproready/schemas/check-report-v1.schema.json"
RETAINED_RULE_IDS = [
    "python.sys-path-three-dot",
    "python.download-comment-http-url",
    "python.open-bundled-archive-member",
    "python.pandas-csv-inventory-absence",
    "python.notebook-pip-install",
    "python.gdown-anonymized-value",
    "python.entry-point-input",
    "python.gfile-bucket-authority",
]


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


def _python_report(
    tmp_path: Path,
    source: str,
    report_validator: Draft202012Validator,
    *,
    name: str = "main.py",
) -> dict[str, object]:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    report = _check_document(path)
    report_validator.validate(report)
    return report


def _notebook_bytes(
    cells: list[dict[str, object]], *, language: str = "python"
) -> bytes:
    document = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"language": language},
            "language_info": {"name": language},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _code_cell(source: str | list[str]) -> dict[str, object]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def _markdown_cell(source: str) -> dict[str, object]:
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def _notebook_report(
    tmp_path: Path,
    cells: list[dict[str, object]],
    report_validator: Draft202012Validator,
    *,
    language: str = "python",
) -> dict[str, object]:
    path = tmp_path / "analysis.ipynb"
    path.write_bytes(_notebook_bytes(cells, language=language))
    report = _check_document(path)
    report_validator.validate(report)
    return report


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return output.getvalue()


def _zip_report(
    tmp_path: Path,
    entries: list[tuple[str, bytes]],
    report_validator: Draft202012Validator,
) -> dict[str, object]:
    path = tmp_path / "artifact.zip"
    path.write_bytes(_zip_bytes(entries))
    report = _check_document(path)
    report_validator.validate(report)
    return report


def test_retained_catalogue_has_frozen_order_and_review_kind(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _python_report(
        tmp_path,
        "from sys import path\npath.append('.../vendor')\n",
        report_validator,
    )

    assert [result["rule_id"] for result in report["rule_results"]] == [
        "archive.structure",
        "python.absolute-path",
        "python.dependencies",
        *RETAINED_RULE_IDS,
    ]
    observations = [
        item for result in report["rule_results"][3:] for item in result["observations"]
    ]
    assert observations
    assert {item["kind"] for item in observations} == {"needs_human_review"}
    assert [item["observation_id"] for item in observations] == [
        f"observation:{index}" for index in range(len(observations))
    ]


def test_sys_path_three_dot_requires_direct_unchanged_from_import(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _python_report(
        tmp_path,
        """from sys import path
path.append('.../vendor/package')
from sys import path as search_path
search_path.append(r'cache\\...\\weights')
path.append('../vendor/package')
path.append('..../vendor/package')
import sys
sys.path.append('.../ignored')
path = object()
path.append('.../rebound')
# path.append('.../comment')
""",
        report_validator,
    )
    result = _rule(report, "python.sys-path-three-dot")

    assert result["status"] == "complete"
    assert [item["condition_code"] for item in result["observations"]] == [
        "three_dot_path_segment",
        "three_dot_path_segment",
    ]
    assert [item["line"] for item in result["observations"]] == [2, 4]
    assert result["evidence"] == []


def test_download_comment_uses_comment_tokens_and_http_url_boundaries(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _python_report(
        tmp_path,
        """# Download from https://example.test/data.zip.
value = '# download https://string.test/file'
download_url = 'https://assignment.test/file'
# optional DOWNLOAD via HTTP://EXAMPLE.TEST/archive
# download this file
# https://split.test/file
# download ftp://example.test/file
""",
        report_validator,
    )
    result = _rule(report, "python.download-comment-http-url")

    assert result["status"] == "complete"
    assert [item["condition_code"] for item in result["observations"]] == [
        "download_comment_with_http_url",
        "download_comment_with_http_url",
    ]
    assert [item["line"] for item in result["observations"]] == [1, 4]
    assert result["observations"][0]["snippet"].endswith("data.zip.")


def test_comment_tokenization_is_independent_of_ast_and_reports_its_own_failure(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    syntax_error = _python_report(
        tmp_path,
        "# download https://example.test/file\nif :\n",
        report_validator,
        name="syntax.py",
    )
    comment = _rule(syntax_error, "python.download-comment-http-url")
    assert comment["status"] == "complete"
    assert len(comment["observations"]) == 1
    assert _rule(syntax_error, "python.sys-path-three-dot")["status"] == "partial"

    tokenize_error = _python_report(
        tmp_path,
        "# download https://example.test/file\nvalue = '''unterminated\n",
        report_validator,
        name="tokenize.py",
    )
    comment = _rule(tokenize_error, "python.download-comment-http-url")
    assert comment["status"] == "partial"
    assert len(comment["observations"]) == 1
    assert [item["reason_code"] for item in comment["failed_inputs"]] == [
        "python_tokenize_error"
    ]


def test_open_reports_unique_descendant_archive_member_with_atomic_evidence(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    notebook = _notebook_bytes(
        [
            _code_cell("dataset = 'data.csv'\n"),
            _code_cell("records = open(dataset, 'rb').read()\n"),
        ]
    )
    nested = _zip_bytes([("data.csv", b"a,b\n1,2\n")])
    report = _zip_report(
        tmp_path,
        [("analysis.ipynb", notebook), ("bundle.zip", nested)],
        report_validator,
    )
    result = _rule(report, "python.open-bundled-archive-member")

    assert result["status"] == "complete"
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["kind"] == "bundled_archive_member"
    assert result["evidence"][0]["value"] == "data.csv"
    assert result["observations"] == [
        {
            "observation_id": result["observations"][0]["observation_id"],
            "rule_id": "python.open-bundled-archive-member",
            "kind": "needs_human_review",
            "condition_code": "read_path_only_in_bundled_archive",
            "member_id": "member:0",
            "source_id": "source:2",
            "line": 1,
            "cell": 2,
            "snippet": "open(dataset, 'rb')",
            "snippet_truncated": False,
            "evidence_ids": [result["evidence"][0]["evidence_id"]],
        }
    ]


@pytest.mark.parametrize(
    "extra_entries",
    [
        [("data.csv", b"outer")],
        [
            ("first.zip", _zip_bytes([("data.csv", b"first")])),
            ("second.zip", _zip_bytes([("data.csv", b"second")])),
        ],
        [("bundle.zip", _zip_bytes([("wrapper/data.csv", b"wrapped")]))],
    ],
)
def test_open_excludes_outer_duplicate_and_wrapper_inventory_relations(
    tmp_path: Path,
    report_validator: Draft202012Validator,
    extra_entries: list[tuple[str, bytes]],
) -> None:
    source = b"open('data.csv')\n"
    base = (
        []
        if any(name.endswith(".zip") for name, _ in extra_entries)
        else [("bundle.zip", _zip_bytes([]))]
    )
    report = _zip_report(
        tmp_path,
        [("main.py", source), *base, *extra_entries],
        report_validator,
    )
    result = _rule(report, "python.open-bundled-archive-member")
    assert result["status"] == "complete"
    assert result["evidence"] == []
    assert result["observations"] == []


def test_pandas_csv_reports_only_paths_absent_from_source_container(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    source = b"""import pandas as pd
missing = 'inputs/review.csv'
pd.read_csv(missing)
pd.read_csv('root.csv')
pd.read_csv('local.csv')
pd = object()
pd.read_csv('rebound.csv')
"""
    report = _zip_report(
        tmp_path,
        [
            ("scripts/main.py", source),
            ("root.csv", b"root"),
            ("scripts/local.csv", b"local"),
            ("inputs/review.xlsx", b"different format"),
        ],
        report_validator,
    )
    result = _rule(report, "python.pandas-csv-inventory-absence")

    assert result["status"] == "complete"
    assert [item["condition_code"] for item in result["observations"]] == [
        "pandas_csv_not_in_inventory"
    ]
    assert result["observations"][0]["line"] == 3
    assert result["evidence"] == []


def test_inventory_relation_rules_are_not_applicable_to_direct_python(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _python_report(
        tmp_path,
        "import pandas as pd\npd.read_csv('data.csv')\nopen('data.csv')\n",
        report_validator,
    )
    for rule_id in (
        "python.open-bundled-archive-member",
        "python.pandas-csv-inventory-absence",
    ):
        result = _rule(report, rule_id)
        assert result["status"] == "not_applicable"
        assert result["observations"] == []


def test_notebook_pip_install_precedes_ast_rejection_and_keeps_coordinates(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _notebook_report(
        tmp_path,
        [
            _markdown_cell("!pip install prose-only"),
            _code_cell(
                ["title = '!pip install string-only'\n", "  !pip install alpha beta\n"]
            ),
            _code_cell(
                "# !pip install comment-only\n%pip install magic\n!pip download alpha\n"
            ),
        ],
        report_validator,
    )
    result = _rule(report, "python.notebook-pip-install")

    assert result["status"] == "complete"
    assert [(item["cell"], item["line"]) for item in result["observations"]] == [(2, 2)]
    assert result["observations"][0]["snippet"] == "  !pip install alpha beta"
    assert all(item["kind"] == "needs_human_review" for item in result["observations"])
    shell_sources = [
        source
        for source in report["source_index"]
        if source["form"] == "notebook_code_cell"
    ]
    assert [source["reason_code"] for source in shell_sources] == [
        "unsupported_notebook_syntax",
        "unsupported_notebook_syntax",
    ]
    dependency = _rule(report, "python.dependencies")
    assert all(
        item["value"] not in {"alpha", "beta"} for item in dependency["evidence"]
    )


def test_notebook_shell_parse_error_is_rule_local(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _notebook_report(
        tmp_path,
        [_code_cell("!pip install 'unterminated\n")],
        report_validator,
    )
    result = _rule(report, "python.notebook-pip-install")
    assert result["status"] == "partial"
    assert [item["reason_code"] for item in result["failed_inputs"]] == [
        "notebook_shell_parse_error"
    ]
    assert result["observations"] == []


def test_gdown_accepts_only_bounded_anonymized_value_flows_and_reuses_evidence(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _python_report(
        tmp_path,
        """import gdown
files = {'train': 'ANONYMIZED', 'test': 'ANONYMIZED'}
gdown.download(files['train'] + '/data')
gdown.download(f"{files['test']}/labels")
url = files['train']
gdown.download(url)
if False:
    gdown.download(files['test'])
other = url
gdown.download(other)
files['train'] = 'concrete'
gdown.download(files['train'])
gdown = object()
gdown.download(files['test'])
""",
        report_validator,
    )
    result = _rule(report, "python.gdown-anonymized-value")

    assert result["status"] == "complete"
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["kind"] == "anonymized_mapping_value"
    assert result["evidence"][0]["value"] == "ANONYMIZED"
    assert [item["line"] for item in result["observations"]] == [3, 4, 6, 8]
    assert {tuple(item["evidence_ids"]) for item in result["observations"]} == {
        (result["evidence"][0]["evidence_id"],)
    }


def test_gdown_notebook_state_crosses_valid_cells_but_not_failed_cells(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    valid = _notebook_report(
        tmp_path,
        [
            _code_cell("import gdown\n"),
            _code_cell("files = {'x': 'ANONYMIZED'}\n"),
            _code_cell("gdown.download(files['x'])\n"),
        ],
        report_validator,
    )
    result = _rule(valid, "python.gdown-anonymized-value")
    assert result["status"] == "complete"
    assert [(item["cell"], item["line"]) for item in result["observations"]] == [(3, 1)]

    invalidated = _notebook_report(
        tmp_path,
        [
            _code_cell("import gdown\nfiles = {'x': 'ANONYMIZED'}\n"),
            _code_cell("if :\n"),
            _code_cell("gdown.download(files['x'])\n"),
        ],
        report_validator,
    )
    result = _rule(invalidated, "python.gdown-anonymized-value")
    assert result["status"] == "partial"
    assert result["observations"] == []

    reestablished = _notebook_report(
        tmp_path,
        [
            _code_cell("!pip install helper\n"),
            _code_cell("import gdown\nfiles = {'x': 'ANONYMIZED'}\n"),
            _code_cell("gdown.download(files['x'])\n"),
        ],
        report_validator,
    )
    result = _rule(reestablished, "python.gdown-anonymized-value")
    assert result["status"] == "partial"
    assert [(item["cell"], item["line"]) for item in result["observations"]] == [(3, 1)]


def test_entry_point_input_requires_canonical_guard_and_unshadowed_builtin(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _python_report(
        tmp_path,
        """if __name__ == '__main__':
    count = int(input('count: '))
    if count:
        value = input()
    def deferred():
        return input()
if '__main__' == __name__:
    input()
def main():
    return input()
if __name__ == '__main__':
    main()
""",
        report_validator,
    )
    result = _rule(report, "python.entry-point-input")

    assert result["status"] == "complete"
    assert [item["line"] for item in result["observations"]] == [2, 4]
    assert all(item["snippet"].startswith("input(") for item in result["observations"])

    shadowed = _python_report(
        tmp_path,
        "input = make_reader()\nif __name__ == '__main__':\n    input()\n",
        report_validator,
        name="shadowed.py",
    )
    assert _rule(shadowed, "python.entry-point-input")["observations"] == []

    star_import = _python_report(
        tmp_path,
        "from helpers import *\nif __name__ == '__main__':\n    input()\n",
        report_validator,
        name="star-import.py",
    )
    result = _rule(star_import, "python.entry-point-input")
    assert [item["line"] for item in result["observations"]] == [3]


def test_entry_point_notebook_respects_prior_binding_and_failed_cell_state(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _notebook_report(
        tmp_path,
        [
            _code_cell("if __name__ == '__main__':\n    input()\n"),
            _code_cell("input = reader\n"),
            _code_cell("if __name__ == '__main__':\n    input()\n"),
        ],
        report_validator,
    )
    result = _rule(report, "python.entry-point-input")
    assert result["status"] == "complete"
    assert [(item["cell"], item["line"]) for item in result["observations"]] == [(1, 2)]

    failed = _notebook_report(
        tmp_path,
        [
            _code_cell("if :\n"),
            _code_cell("if __name__ == '__main__':\n    input()\n"),
        ],
        report_validator,
    )
    result = _rule(failed, "python.entry-point-input")
    assert result["status"] == "partial"
    assert result["observations"] == []


def test_gfile_bucket_authority_resolves_direct_and_join_imports_exactly(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    report = _python_report(
        tmp_path,
        """from tensorflow.io.gfile import GFile as Reader
from os.path import join as combine
Reader('gs://bucket/cache.bin', 'rb')
Reader(combine('gs://bucket', 'nested.bin'))
import tensorflow as tf
import os as operating_system
tf.io.gfile.GFile(operating_system.path.join('gs://bucket', 'third.bin'))
Reader('gs://real-bucket/cache.bin')
Reader('GS://bucket/cache.bin')
Reader('gs://bucket:443/cache.bin')
Reader('gs://user@bucket/cache.bin')
Reader(file='gs://bucket/keyword.bin')
Reader = object()
Reader('gs://bucket/rebound.bin')
""",
        report_validator,
    )
    result = _rule(report, "python.gfile-bucket-authority")

    assert result["status"] == "complete"
    assert [item["line"] for item in result["observations"]] == [3, 4, 7]
    assert {item["condition_code"] for item in result["observations"]} == {
        "bucket_authority_literal"
    }


def test_rule_applicability_and_parser_failures_are_selector_specific(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    malformed = tmp_path / "broken.ipynb"
    malformed.write_text("{not json", encoding="utf-8")
    report = _check_document(malformed)
    report_validator.validate(report)
    for rule_id in (
        "python.download-comment-http-url",
        "python.notebook-pip-install",
        "python.gdown-anonymized-value",
        "python.entry-point-input",
    ):
        assert _rule(report, rule_id)["status"] == "partial"
    for rule_id in (
        "python.sys-path-three-dot",
        "python.open-bundled-archive-member",
        "python.pandas-csv-inventory-absence",
        "python.gfile-bucket-authority",
    ):
        assert _rule(report, rule_id)["status"] == "not_applicable"

    unsupported = _notebook_report(
        tmp_path,
        [_code_cell("# download https://example.test/file\n")],
        report_validator,
        language="r",
    )
    for rule_id in RETAINED_RULE_IDS:
        assert _rule(unsupported, rule_id)["status"] == "not_applicable"


def test_retained_results_are_deterministic_and_path_free(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    source = """# download https://example.test/archive
from sys import path
path.append('.../vendor')
"""
    path = tmp_path / "stable.py"
    path.write_text(source, encoding="utf-8")

    first = _check_document(path)
    second = _check_document(path)
    report_validator.validate(first)
    assert first == second
    assert str(tmp_path) not in json.dumps(first, ensure_ascii=True)


def test_early_retained_rule_owns_one_observation_limit_terminal(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "early-limit.py"
    path.write_text(
        "from sys import path\n"
        + "\n".join(f"path.append('.../vendor-{index}')" for index in range(4))
        + "\n# download https://example.test/archive\n",
        encoding="utf-8",
    )
    engine = InspectionEngine(
        _snapshot(path),
        lambda _checkpoint: None,
        tmp_path / "early-limit-cache",
    )
    engine.records = _BoundedRecords(
        max_report_bytes=FIXED_LIMITS["max_report_bytes"],
        max_observations=3,
    )

    report = engine.inspect()
    report_validator.validate(report)
    sys_path = _rule(report, "python.sys-path-three-dot")
    assert [item["condition_code"] for item in sys_path["observations"]] == [
        "three_dot_path_segment",
        "three_dot_path_segment",
        "resource_limit_reached",
    ]
    comment = _rule(report, "python.download-comment-http-url")
    assert comment["status"] == "partial"
    assert comment["observations"] == []
    assert [item["reason_code"] for item in comment["skipped_inputs"]] == [
        "observation_limit"
    ]
    terminals = [
        item
        for result in report["rule_results"]
        for item in result["observations"]
        if item["condition_code"] == "resource_limit_reached"
    ]
    assert len(terminals) == 1


def test_late_retained_rule_owns_one_report_limit_terminal(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    source = "from tensorflow.io.gfile import GFile\n" + "\n".join(
        f"GFile('gs://bucket/{'x' * 180}-{index:03d}')" for index in range(100)
    )
    path = tmp_path / "late-limit.zip"
    path.write_bytes(
        _zip_bytes(
            [
                ("main.py", (source + "\n").encode()),
                ("requirements.txt", b"tensorflow\n"),
            ]
        )
    )
    cache = tmp_path / "late-limit-cache"
    cache.mkdir()
    engine = InspectionEngine(
        _snapshot(path),
        lambda _checkpoint: None,
        cache,
    )
    engine.records = _BoundedRecords(
        max_report_bytes=24 * 1024,
        max_observations=FIXED_LIMITS["max_observations"],
        reserve_bytes=8 * 1024,
    )

    report = engine.inspect()
    report_validator.validate(report)
    result = _rule(report, "python.gfile-bucket-authority")
    assert result["status"] == "partial"
    assert result["observations"][-1]["condition_code"] == "resource_limit_reached"
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "report_size_limit"
    ]
    terminals = [
        item
        for rule in report["rule_results"]
        for item in rule["observations"]
        if item["condition_code"] == "resource_limit_reached"
    ]
    assert len(terminals) == 1


def test_candidate_cap_retains_deterministic_prefix_and_one_terminal(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "candidate-limit.py"
    path.write_text(
        "\n".join(
            f"# download https://example.test/archive-{index:05d}"
            for index in range(FIXED_LIMITS["max_observations"])
        )
        + "\n",
        encoding="utf-8",
    )

    report = _check_document(path)
    report_validator.validate(report)
    result = _rule(report, "python.download-comment-http-url")
    assert result["status"] == "partial"
    assert len(result["observations"]) == FIXED_LIMITS["max_observations"]
    assert result["observations"][0]["snippet"].endswith("archive-00000")
    assert result["observations"][-2]["snippet"].endswith("archive-09998")
    assert result["observations"][-1]["condition_code"] == "resource_limit_reached"
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "observation_limit"
    ]

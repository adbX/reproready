"""Behavioral contracts for Python literal absolute-path observations."""

from __future__ import annotations

import hashlib
import json
import zipfile
from io import StringIO
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from rich.console import Console

from reproready.checker import encode_intake_report, intake_report
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
        if result["rule_id"] == "python.absolute-path"
    )


def _python_report(
    tmp_path: Path,
    source: str,
    validator: Draft202012Validator,
    *,
    name: str = "paths.py",
) -> dict[str, object]:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    report = intake_report(path)
    validator.validate(report)
    return report


def _snapshot(path: Path) -> SourceSnapshot:
    body = path.read_bytes()
    return SourceSnapshot(path.name, len(body), hashlib.sha256(body).hexdigest(), path)


def _write_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body)


def _notebook(cells: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "cells": cells,
            "metadata": {
                "kernelspec": {"language": "python", "name": "python"},
                "checker_decoy": "open('/ignore/raw-json')",
            },
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
        width=240,
    )
    render_report(report, console)
    return output.getvalue()


def test_every_frozen_callee_and_argument_slot_is_observed(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    calls: list[tuple[str, int]] = [
        ("open('/p0')", 1),
        ("open(file='/p1')", 1),
        ("io.open('/p2')", 1),
        ("io.open(file='/p3')", 1),
    ]
    one_path_calls = (
        "os.chdir",
        "os.listdir",
        "os.scandir",
        "os.mkdir",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.stat",
        "os.lstat",
        "os.access",
    )
    next_value = 4
    for callee in one_path_calls:
        calls.extend(
            [
                (f"{callee}('/p{next_value}')", 1),
                (f"{callee}(path='/p{next_value + 1}')", 1),
            ]
        )
        next_value += 2
    calls.extend(
        [
            (f"os.makedirs('/p{next_value}')", 1),
            (f"os.makedirs(name='/p{next_value + 1}')", 1),
            (f"os.rename('/p{next_value + 2}', '/p{next_value + 3}')", 2),
            (
                f"os.rename(src='/p{next_value + 4}', dst='/p{next_value + 5}')",
                2,
            ),
            (f"os.replace('/p{next_value + 6}', '/p{next_value + 7}')", 2),
            (
                f"os.replace(src='/p{next_value + 8}', dst='/p{next_value + 9}')",
                2,
            ),
            (f"os.walk('/p{next_value + 10}')", 1),
            (f"os.walk(top='/p{next_value + 11}')", 1),
        ]
    )
    next_value += 12
    for callee in (
        "os.path.exists",
        "os.path.isfile",
        "os.path.isdir",
        "os.path.getsize",
    ):
        calls.extend(
            [
                (f"{callee}('/p{next_value}')", 1),
                (f"{callee}(path='/p{next_value + 1}')", 1),
            ]
        )
        next_value += 2

    source = "\n".join(call for call, _ in calls) + "\n"
    result = _rule(_python_report(tmp_path, source, report_validator))

    expected = [
        (line, call)
        for line, (call, count) in enumerate(calls, start=1)
        for _ in range(count)
    ]
    assert [(item["line"], item["snippet"]) for item in result["observations"]] == (
        expected
    )
    assert {item["condition_code"] for item in result["observations"]} == {
        "posix_absolute_path"
    }
    assert result["status"] == "complete"


def test_path_constructors_match_only_exact_supported_positional_spelling(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    constructors = (
        "Path",
        "PurePath",
        "PosixPath",
        "PurePosixPath",
        "WindowsPath",
        "PureWindowsPath",
    )
    positives = [f"{name}('/{name}')" for name in constructors]
    positives += [f"pathlib.{name}('/qualified-{name}')" for name in constructors]
    negatives = [
        "p.Path('/alias')",
        "package.pathlib.Path('/longer-chain')",
        "Path.open('/method')",
        "obj.open('/method')",
        "builtins.open('/qualified-open')",
        "Path(path='/keyword')",
        "pathlib.Path(path='/qualified-keyword')",
        "chdir('/imported-alias')",
    ]

    result = _rule(
        _python_report(
            tmp_path,
            "\n".join(positives + negatives) + "\n",
            report_validator,
        )
    )
    assert [item["snippet"] for item in result["observations"]] == positives


def test_path_classes_follow_frozen_precedence_and_negative_boundaries(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    positives = [
        ("\\\\?\\C:\\device", "windows_device_path"),
        ("\\\\.\\PIPE\\name", "windows_device_path"),
        ("\\\\server\\share", "windows_unc_path"),
        ("//server/share", "windows_unc_path"),
        ("C:/work", "windows_drive_path"),
        ("z:\\work", "windows_drive_path"),
        ("/work", "posix_absolute_path"),
        ("///work", "posix_absolute_path"),
        ("//", "posix_absolute_path"),
        ("~", "tilde_path"),
        ("~/work", "tilde_path"),
        ("~\\work", "tilde_path"),
        ("~user/work", "tilde_path"),
        ("~user\\work", "tilde_path"),
    ]
    negatives = [
        "",
        "relative/work",
        "C:work",
        "file:///tmp/work",
        "~user",
        "/\\mixed",
        "\\/mixed",
        "é:/work",
        "\\\\",
    ]
    source = "\n".join(
        [f"open({value!r})" for value, _ in positives]
        + [f"open({value!r})" for value in negatives]
    )

    result = _rule(_python_report(tmp_path, source + "\n", report_validator))
    assert [item["condition_code"] for item in result["observations"]] == [
        condition for _, condition in positives
    ]
    assert [item["snippet"] for item in result["observations"]] == [
        f"open({value!r})" for value, _ in positives
    ]


def test_only_decoded_constant_strings_are_observed(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    source = """\
name = '/name'
open('/adjacent' '/literal')
open(b'/bytes')
open(f'/f-string/{name}')
open(name)
open('/left' + '/right')
open('/percent/%s' % name)
open('/format/{}'.format(name))
open(*('/starred',))
open(**{'file': '/kwargs'})
"""
    result = _rule(_python_report(tmp_path, source, report_validator))
    assert [item["snippet"] for item in result["observations"]] == [
        "open('/adjacent' '/literal')"
    ]


def test_shadowed_and_unreachable_calls_remain_lexical_observations(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    source = """\
open = lambda value: value
open('/shadowed')
if False:
    open('/unreachable')
os = None
os.chdir('/shadowed-os')
"""
    result = _rule(_python_report(tmp_path, source, report_validator))
    assert [item["snippet"] for item in result["observations"]] == [
        "open('/shadowed')",
        "open('/unreachable')",
        "os.chdir('/shadowed-os')",
    ]


def test_direct_and_notebook_locations_use_exact_full_call_syntax(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    direct_source = "value = 1\nopen(\n    '/direct/path',\n    encoding='utf-8',\n)\n"
    direct = _rule(_python_report(tmp_path, direct_source, report_validator))
    assert direct["observations"] == [
        {
            "observation_id": "observation:0",
            "rule_id": "python.absolute-path",
            "kind": "finding",
            "condition_code": "posix_absolute_path",
            "member_id": None,
            "source_id": "source:0",
            "line": 2,
            "cell": None,
            "snippet": "open(\n    '/direct/path',\n    encoding='utf-8',\n)",
            "snippet_truncated": False,
            "evidence_ids": [],
        }
    ]

    notebook = _notebook(
        [
            {"cell_type": "markdown", "metadata": {}, "source": "open('/markdown')"},
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": "open('/output')",
                    }
                ],
                "source": "value = 1\nopen('/notebook/path')\n",
            },
            {"cell_type": "raw", "metadata": {}, "source": "open('/raw')"},
        ]
    )
    archive_path = tmp_path / "notebook.zip"
    _write_zip(archive_path, [("analysis.ipynb", notebook)])
    report = intake_report(archive_path)
    report_validator.validate(report)
    observation = _rule(report)["observations"][0]
    assert (
        observation["source_id"],
        observation["member_id"],
        observation["cell"],
        observation["line"],
        observation["snippet"],
    ) == (
        "source:1",
        "member:0",
        2,
        2,
        "open('/notebook/path')",
    )
    encoded = encode_intake_report(archive_path)
    for decoy in (b"/markdown", b"/output", b"/raw", b"/ignore/raw-json"):
        assert decoy not in encoded
    assert str(tmp_path).encode() not in encoded


def test_fixture_notebook_excludes_outputs_raw_cells_and_arbitrary_strings(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    report = intake_report(checker_inputs.paths["valid_notebook"])
    report_validator.validate(report)
    result = _rule(report)

    assert [
        (item["cell"], item["line"], item["condition_code"], item["snippet"])
        for item in result["observations"]
    ] == [
        (1, 2, "posix_absolute_path", "open('/srv/input.csv')"),
        (
            1,
            3,
            "windows_drive_path",
            "pathlib.Path(r'C:\\notebook\\data')",
        ),
    ]
    encoded = encode_report(report)
    for decoy in (
        b"/ignore/output-only",
        b"/ignore/raw-cell",
        b"/ignore/arbitrary-string",
        b"/ignore/raw-json",
    ):
        assert decoy not in encoded


def test_snippet_bound_and_terminal_escaping_preserve_complete_coverage(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    long_path = "/" + "x" * 300
    long_result = _rule(
        _python_report(tmp_path, f"open({long_path!r})\n", report_validator)
    )
    observation = long_result["observations"][0]
    assert len(observation["snippet"]) == 240
    assert observation["snippet_truncated"] is True
    assert long_result["status"] == "complete"

    hostile_path = "/[bold]unsafe[/bold]\u0007\u202e"
    hostile_report = _python_report(
        tmp_path,
        f"open('{hostile_path}')\n",
        report_validator,
        name="hostile.py",
    )
    output = _render(hostile_report)
    assert "python.absolute-path: complete" in output
    assert "posix_absolute_path [source:0, line:1]" in output
    assert "[bold]unsafe[/bold]\\u0007\\u202e" in output
    assert "\u0007" not in output
    assert "\u202e" not in output


def test_observations_sort_by_source_location_and_follow_archive_ids(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    notebook = _notebook(
        [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": "open('/cell-one')\n",
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": "os.rename('/cell-two-src', 'C:/cell-two-dst')\n",
            },
        ]
    )
    archive_path = tmp_path / "ordered.zip"
    _write_zip(
        archive_path,
        [
            ("/[bad].py", b"open('/first')\n"),
            ("analysis.ipynb", notebook),
            ("last.py", b"open('~/last')\n"),
        ],
    )

    report = intake_report(archive_path)
    report_validator.validate(report)
    archive_observations = report["rule_results"][0]["observations"]
    path_observations = _rule(report)["observations"]
    assert [item["source_id"] for item in path_observations] == [
        "source:0",
        "source:2",
        "source:3",
        "source:3",
        "source:4",
    ]
    assert [item["cell"] for item in path_observations] == [None, 1, 2, 2, None]
    all_observations = archive_observations + path_observations
    assert [item["observation_id"] for item in all_observations] == [
        f"observation:{index}" for index in range(len(all_observations))
    ]


def test_path_statuses_and_coverage_are_independent_from_findings(
    tmp_path: Path,
    checker_inputs,
    report_validator: Draft202012Validator,
) -> None:
    complete = _rule(
        _python_report(tmp_path, "value = 1\n", report_validator, name="complete.py")
    )
    not_applicable = _rule(intake_report(checker_inputs.paths["requirements"]))
    unsupported = _rule(intake_report(checker_inputs.paths["unsupported_regular"]))
    malformed = _rule(intake_report(checker_inputs.paths["malformed_notebook"]))
    non_python = _rule(intake_report(checker_inputs.paths["unsupported_notebook"]))
    no_code = _rule(intake_report(checker_inputs.paths["no_code_notebook"]))

    assert complete["status"] == "complete"
    assert not_applicable["status"] == "not_applicable"
    assert unsupported["status"] == "unsupported"
    assert malformed["status"] == "partial"
    assert [item["reason_code"] for item in malformed["failed_inputs"]] == [
        "notebook_parse_error"
    ]
    assert non_python["status"] == "not_applicable"
    assert no_code["status"] == "not_applicable"

    archive_path = tmp_path / "mixed-source-results.zip"
    _write_zip(
        archive_path,
        [("good.py", b"open('/kept')\n"), ("broken.py", b"def broken(:\n")],
    )
    mixed = intake_report(archive_path)
    report_validator.validate(mixed)
    result = _rule(mixed)
    assert result["status"] == "partial"
    assert [item["snippet"] for item in result["observations"]] == ["open('/kept')"]
    assert [item["reason_code"] for item in result["failed_inputs"]] == ["syntax_error"]

    discovery = _rule(
        intake_report(checker_inputs.paths["central_directory_error_zip"])
    )
    assert discovery["status"] == "partial"
    assert any(
        item["reason_code"] == "central_directory_error" and item["source_id"] is None
        for item in discovery["failed_inputs"]
    )


def test_observation_cap_attaches_one_terminal_path_record(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "observation-limit.py"
    path.write_text("\n".join(f"open('/path-{index}')" for index in range(5)))
    engine = InspectionEngine(
        _snapshot(path), lambda _checkpoint: None, tmp_path / "observation-cache"
    )
    engine.records = _BoundedRecords(
        max_report_bytes=FIXED_LIMITS["max_report_bytes"],
        max_observations=3,
    )

    report = engine.inspect()
    report_validator.validate(report)
    result = _rule(report)
    assert len(result["observations"]) == 3
    assert [item["condition_code"] for item in result["observations"]] == [
        "posix_absolute_path",
        "posix_absolute_path",
        "resource_limit_reached",
    ]
    assert result["observations"][-1]["line"] == 3
    assert [item["reason_code"] for item in result["skipped_inputs"]] == [
        "observation_limit"
    ]
    assert report["limits"]["reached"] == ["max_observations"]
    assert result["status"] == "partial"


def test_archive_observations_have_priority_over_path_observations_at_cap(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "mixed-limit.zip"
    _write_zip(path, [("/bad\u0007.py", b"open('/omitted-path')\n")])
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    engine = InspectionEngine(_snapshot(path), lambda _checkpoint: None, cache_root)
    engine.records = _BoundedRecords(
        max_report_bytes=FIXED_LIMITS["max_report_bytes"],
        max_observations=3,
    )

    report = engine.inspect()
    report_validator.validate(report)
    archive_result = report["rule_results"][0]
    path_result = _rule(report)
    assert [item["condition_code"] for item in archive_result["observations"]] == [
        "absolute_member_path",
        "control_character_in_name",
    ]
    assert [item["condition_code"] for item in path_result["observations"]] == [
        "resource_limit_reached"
    ]
    assert path_result["observations"][0]["source_id"] == "source:0"
    assert [
        item["observation_id"]
        for result in report["rule_results"]
        for item in result["observations"]
    ] == ["observation:0", "observation:1", "observation:2"]


def test_report_cap_keeps_a_bounded_path_prefix_without_dangling_identity(
    tmp_path: Path, report_validator: Draft202012Validator
) -> None:
    path = tmp_path / "report-limit.py"
    source = "\n".join(f"open('/{'x' * 210}-{index:03d}')" for index in range(80))
    path.write_text(source, encoding="utf-8")
    engine = InspectionEngine(
        _snapshot(path), lambda _checkpoint: None, tmp_path / "cache"
    )
    engine.records = _BoundedRecords(
        max_report_bytes=16 * 1024,
        max_observations=FIXED_LIMITS["max_observations"],
        reserve_bytes=8 * 1024,
    )

    report = engine.inspect()
    encoded = encode_report(report)
    report_validator.validate(report)
    result = _rule(report)
    source_ids = {item["source_id"] for item in report["source_index"]}
    assert len(encoded) <= engine.records.max_report_bytes
    assert report["limits"]["reached"] == ["max_report_bytes"]
    assert 0 < len(result["observations"]) < 80
    assert result["observations"][-1]["condition_code"] == "resource_limit_reached"
    assert all(
        item["source_id"] is None or item["source_id"] in source_ids
        for item in result["observations"]
        + result["skipped_inputs"]
        + result["failed_inputs"]
    )


def test_repeated_runs_have_identical_path_results_and_encoded_fields(
    checker_inputs, report_validator: Draft202012Validator
) -> None:
    path = checker_inputs.paths["absolute_paths"]
    first = intake_report(path)
    second = intake_report(path)
    report_validator.validate(first)
    assert first == second
    assert encode_intake_report(path) == encode_intake_report(path)
    assert _rule(first)["status"] == "complete"

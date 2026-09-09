"""Behavioral tests for checker source and worker containment."""

from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path

import pytest
from checker_input_fixtures import mutate_source_same_size

from reproready.checker_intake import (
    FIXED_LIMITS,
    InputRejectedError,
    SnapshotError,
    WorkerCheckpoint,
    run_inspection_child,
    snapshot_source,
)


def _snapshot_report(path: Path, checkpoint: WorkerCheckpoint) -> bytes:
    body = path.read_bytes()
    checkpoint("after_source_read")
    return json.dumps(
        {"body": body.decode("utf-8"), "snapshot_name": path.name},
        sort_keys=True,
    ).encode("utf-8")


def _hang_after_source_read(path: Path, checkpoint: WorkerCheckpoint) -> bytes:
    path.read_bytes()
    checkpoint("after_source_read")
    threading.Event().wait()
    raise AssertionError("unreachable")


def _crash_worker(path: Path, checkpoint: WorkerCheckpoint) -> bytes:
    path.read_bytes()
    checkpoint("after_source_read")
    raise RuntimeError("synthetic worker failure")


def _assert_no_inspection_child() -> None:
    assert all(
        child.name != "reproready-inspector"
        for child in multiprocessing.active_children()
    )


def test_snapshot_is_descriptor_verified_and_isolated(
    checker_inputs,
    tmp_path: Path,
) -> None:
    fixture = checker_inputs.paths["minimal_python"]
    source = tmp_path / fixture.name
    original = fixture.read_bytes()
    source.write_bytes(original)

    with snapshot_source(source) as snapshot:
        assert snapshot.display_name == source.name
        assert snapshot.size_bytes == len(original)
        assert snapshot.path.read_bytes() == original
        assert snapshot.path.stat().st_mode & 0o777 == 0o400

        source.write_bytes(b"value = 9\n")
        assert snapshot.path.read_bytes() == original


def test_snapshot_does_not_write_the_source(checker_inputs, tmp_path: Path) -> None:
    fixture = checker_inputs.paths["minimal_python"]
    source = tmp_path / fixture.name
    original = fixture.read_bytes()
    source.write_bytes(original)
    before = source.stat()

    with snapshot_source(source):
        pass

    after = source.stat()
    assert source.read_bytes() == original
    assert (after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )


def test_same_inode_mutation_during_copy_is_rejected(
    checker_inputs,
    tmp_path: Path,
) -> None:
    fixture = checker_inputs.paths["mutable_source"]
    source = tmp_path / fixture.name
    source.write_bytes(fixture.read_bytes())
    seen_checkpoints: list[str] = []

    def mutate(checkpoint: str) -> None:
        seen_checkpoints.append(checkpoint)
        mutate_source_same_size(source)

    with (
        pytest.raises(SnapshotError) as raised,
        snapshot_source(source, _checkpoint=mutate),
    ):
        pytest.fail("a changed source must not yield a snapshot")

    assert seen_checkpoints == [checker_inputs.mutation_checkpoint]
    assert raised.value.code == "source_changed"


def test_path_identity_change_during_copy_is_rejected(
    checker_inputs,
    tmp_path: Path,
) -> None:
    fixture = checker_inputs.paths["mutable_source"]
    source = tmp_path / fixture.name
    source.write_bytes(fixture.read_bytes())
    replacement = tmp_path / "replacement.py"
    replacement.write_bytes(b"value = 3\n")
    seen_checkpoints: list[str] = []

    def replace(checkpoint: str) -> None:
        seen_checkpoints.append(checkpoint)
        replacement.replace(source)

    with (
        pytest.raises(SnapshotError) as raised,
        snapshot_source(source, _checkpoint=replace),
    ):
        pytest.fail("a replaced source must not yield a snapshot")

    assert seen_checkpoints == [checker_inputs.mutation_checkpoint]
    assert raised.value.code == "source_changed"


@pytest.mark.parametrize("case", ["top_level_link", "directory"])
def test_links_and_non_regular_inputs_are_rejected_before_read(
    checker_inputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    source = checker_inputs.paths["top_level_link"]
    if case == "directory":
        source = tmp_path / "directory"
        source.mkdir()

    def unexpected_read(*args, **kwargs):
        pytest.fail("rejected input content was read")

    monkeypatch.setattr("reproready.checker_intake.os.read", unexpected_read)
    with pytest.raises(InputRejectedError) as raised, snapshot_source(source):
        pytest.fail("a rejected input must not yield a snapshot")

    expected = "top_level_link" if case == "top_level_link" else "not_regular_file"
    assert raised.value.code == expected


def test_unsupported_system_is_rejected_before_path_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("reproready.checker_intake.sys.platform", "win32")

    with (
        pytest.raises(InputRejectedError) as raised,
        snapshot_source("does-not-exist.py"),
    ):
        pytest.fail("an unsupported system must not inspect the path")

    assert raised.value.code == "unsupported_system"


def test_input_limit_is_checked_from_descriptor_metadata(tmp_path: Path) -> None:
    source = tmp_path / "too-large.py"
    with source.open("wb") as handle:
        handle.truncate(FIXED_LIMITS["max_input_bytes"] + 1)

    with pytest.raises(SnapshotError) as raised, snapshot_source(source):
        pytest.fail("an oversized source must not be copied")

    assert raised.value.code == "max_input_bytes"


def test_inspection_child_reads_only_the_completed_snapshot(
    checker_inputs,
    tmp_path: Path,
) -> None:
    fixture = checker_inputs.paths["minimal_python"]
    source = tmp_path / fixture.name
    source.write_bytes(fixture.read_bytes())
    with snapshot_source(source) as snapshot:
        source.write_bytes(b"value = 9\n")
        outcome = run_inspection_child(snapshot, _snapshot_report)

    assert outcome.status == "complete"
    assert outcome.issue_code is None
    assert outcome.reached_limit is None
    assert outcome.last_checkpoint == checker_inputs.timeout_checkpoint
    assert outcome.report_bytes is not None
    assert json.loads(outcome.report_bytes) == {
        "body": "value = 1\n",
        "snapshot_name": "source.snapshot",
    }
    _assert_no_inspection_child()


def test_parent_kills_worker_at_elapsed_limit(checker_inputs) -> None:
    source = checker_inputs.paths["minimal_python"]

    with snapshot_source(source) as snapshot:
        outcome = run_inspection_child(
            snapshot,
            _hang_after_source_read,
            _max_elapsed_seconds=1.0,
        )

    assert outcome.status == "error"
    assert outcome.issue_code == "worker_timeout"
    assert outcome.reached_limit == "max_elapsed_seconds"
    assert outcome.last_checkpoint == checker_inputs.timeout_checkpoint
    assert outcome.report_bytes is None
    _assert_no_inspection_child()


def test_parent_kills_worker_at_resident_memory_limit(checker_inputs) -> None:
    source = checker_inputs.paths["minimal_python"]

    with snapshot_source(source) as snapshot:
        outcome = run_inspection_child(
            snapshot,
            _hang_after_source_read,
            _max_elapsed_seconds=2.0,
            _max_worker_rss_bytes=1,
        )

    assert outcome.status == "error"
    assert outcome.issue_code == "worker_memory_limit"
    assert outcome.reached_limit == "max_worker_rss_bytes"
    assert outcome.report_bytes is None
    _assert_no_inspection_child()


def test_worker_crash_is_contained_without_a_report(checker_inputs) -> None:
    source = checker_inputs.paths["minimal_python"]

    with snapshot_source(source) as snapshot:
        outcome = run_inspection_child(snapshot, _crash_worker)

    assert outcome.status == "error"
    assert outcome.issue_code == "worker_error"
    assert outcome.reached_limit is None
    assert outcome.last_checkpoint == checker_inputs.timeout_checkpoint
    assert outcome.report_bytes is None
    _assert_no_inspection_child()

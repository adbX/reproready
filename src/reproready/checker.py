"""Internal report pipeline while public checker rules remain under development."""

from __future__ import annotations

import json
import os
from functools import partial
from pathlib import Path

from .checker_intake import (
    InputRejectedError,
    InspectionWorker,
    SnapshotCheckpoint,
    SnapshotError,
    SourceSnapshot,
    WorkerCheckpoint,
    run_inspection_child,
    snapshot_source,
)
from .checker_inventory import InspectionEngine
from .checker_report import (
    classify_name,
    encode_report,
    incomplete_snapshot_report,
    worker_failure_report,
)


def _report_worker(
    path: Path,
    checkpoint: WorkerCheckpoint,
    *,
    display_name: str,
    size_bytes: int,
    sha256: str,
) -> bytes:
    snapshot = SourceSnapshot(display_name, size_bytes, sha256, path)
    cache_root = path.with_name("intake-cache")
    cache_root.mkdir(mode=0o700)
    engine = InspectionEngine(snapshot, checkpoint, cache_root)
    checkpoint(f"detected_kind:{engine.detected_kind}")
    report = engine.inspect()
    return encode_report(report)


def _safe_input_identity(source: str | os.PathLike[str]) -> tuple[str, int]:
    path = Path(source)
    display_name = path.name or "input"
    try:
        size_bytes = path.lstat().st_size
    except OSError:
        size_bytes = 0
    return display_name, max(0, size_bytes)


def intake_report(
    source: str | os.PathLike[str],
    *,
    _worker: InspectionWorker | None = None,
    _snapshot_checkpoint: SnapshotCheckpoint | None = None,
    _max_elapsed_seconds: float | None = None,
    _max_worker_rss_bytes: int | None = None,
) -> dict[str, object]:
    """Produce one schema-shaped bounded-intake report without exposing checker API."""

    display_name, size_bytes = _safe_input_identity(source)
    try:
        with snapshot_source(source, _checkpoint=_snapshot_checkpoint) as snapshot:
            worker: InspectionWorker = _worker or partial(
                _report_worker,
                display_name=snapshot.display_name,
                size_bytes=snapshot.size_bytes,
                sha256=snapshot.sha256,
            )
            outcome = run_inspection_child(
                snapshot,
                worker,
                _max_elapsed_seconds=_max_elapsed_seconds,
                _max_worker_rss_bytes=_max_worker_rss_bytes,
            )
            if outcome.report_bytes is not None:
                report = json.loads(outcome.report_bytes)
                if not isinstance(report, dict):
                    raise RuntimeError("inspection worker returned a non-object report")
                return report
            detected_kind = None
            if (
                outcome.last_checkpoint is not None
                and outcome.last_checkpoint.startswith("detected_kind:")
            ):
                detected_kind = outcome.last_checkpoint.split(":", 1)[1]
            detected_kind = detected_kind or classify_name(snapshot.display_name)
            if detected_kind is None:
                raise RuntimeError(
                    "worker failed before content-dependent classification completed"
                )
            return worker_failure_report(
                snapshot,
                outcome,
                detected_kind=detected_kind,
            )
    except SnapshotError as error:
        reached = "max_input_bytes" if error.code == "max_input_bytes" else None
        return incomplete_snapshot_report(
            display_name=display_name,
            size_bytes=size_bytes,
            issue_code=error.code,
            message=error.message,
            reached_limit=reached,
        )
    except InputRejectedError:
        raise


def encode_intake_report(
    source: str | os.PathLike[str],
    **options: object,
) -> bytes:
    """Serialize one internal intake report deterministically."""

    return encode_report(intake_report(source, **options))

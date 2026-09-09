"""Safe source snapshotting and child-process containment for the checker."""

from __future__ import annotations

import ctypes
import hashlib
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn

_CHECKPOINT_AFTER_SNAPSHOT_COPY = "after_snapshot_copy"
_WORKER_INTERNAL_EXIT = 70
_WORKER_REPORT_LIMIT_EXIT = 71
_COPY_CHUNK_BYTES = 1024 * 1024
_MONITOR_INTERVAL_SECONDS = 0.01
_MAX_MONITOR_MISSES = 3

FIXED_LIMITS = MappingProxyType(
    {
        "max_input_bytes": 2 * 1024**3,
        "max_member_count": 100_000,
        "max_nested_zip_depth": 3,
        "max_expanded_bytes_per_member": 512 * 1024**2,
        "max_expanded_bytes_total": 4 * 1024**3,
        "max_python_source_bytes": 8 * 1024**2,
        "max_notebook_bytes": 32 * 1024**2,
        "max_dependency_file_bytes": 8 * 1024**2,
        "max_worker_rss_bytes": 1024**3,
        "max_temporary_bytes": 4 * 1024**3,
        "max_elapsed_seconds": 120,
        "max_observations": 10_000,
        "max_report_bytes": 64 * 1024**2,
        "max_snippet_codepoints": 240,
    }
)

SnapshotCheckpoint = Callable[[str], None]
WorkerCheckpoint = Callable[[str], None]
InspectionWorker = Callable[[Path, WorkerCheckpoint], bytes]


class IntakeError(Exception):
    """An expected parent-side intake failure with a stable public reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InputRejectedError(IntakeError):
    """A top-level input that must be rejected before content inspection."""


class SnapshotError(IntakeError):
    """A regular input for which no completed snapshot can be produced."""


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Checker-owned immutable copy; valid only inside ``snapshot_source``."""

    display_name: str
    size_bytes: int
    sha256: str
    path: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    """Bounded parent-side result from one inspection child."""

    status: str
    issue_code: str | None
    reached_limit: str | None
    last_checkpoint: str | None
    report_bytes: bytes | None = field(default=None, repr=False)


def _require_supported_system() -> None:
    if sys.platform not in {"darwin", "linux"}:
        raise InputRejectedError(
            "unsupported_system",
            "The checker supports only macOS and Linux.",
        )


def _descriptor_identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _source_state(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _lstat_source(path: Path) -> os.stat_result:
    try:
        source_stat = path.lstat()
    except FileNotFoundError as exc:
        raise InputRejectedError(
            "source_not_found",
            "The input does not exist.",
        ) from exc
    except OSError as exc:
        raise InputRejectedError(
            "source_not_accessible",
            "The input metadata could not be read.",
        ) from exc

    if stat.S_ISLNK(source_stat.st_mode):
        raise InputRejectedError(
            "top_level_link",
            "A top-level symbolic link is not a supported checker input.",
        )
    if not stat.S_ISREG(source_stat.st_mode):
        raise InputRejectedError(
            "not_regular_file",
            "The checker accepts one regular file.",
        )
    return source_stat


def _open_source(path: Path, admitted: os.stat_result) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InputRejectedError(
            "source_not_accessible",
            "The regular input could not be opened without following links.",
        ) from exc

    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        raise InputRejectedError(
            "not_regular_file",
            "The checker accepts one regular file.",
        )
    if _descriptor_identity(admitted) != _descriptor_identity(opened):
        os.close(descriptor)
        raise SnapshotError(
            "source_changed",
            "The source identity or modification state changed while it was opened.",
        )
    return descriptor, opened


def _source_still_matches(path: Path, expected: os.stat_result) -> bool:
    try:
        current_path = path.lstat()
    except OSError:
        return False
    return _source_state(current_path) == _source_state(expected)


@contextmanager
def snapshot_source(
    source: str | os.PathLike[str],
    *,
    _checkpoint: SnapshotCheckpoint | None = None,
) -> Iterator[SourceSnapshot]:
    """Admit one regular file and yield a verified checker-owned snapshot."""

    _require_supported_system()
    path = Path(source)
    admitted = _lstat_source(path)
    descriptor, before = _open_source(path, admitted)

    try:
        if before.st_size > FIXED_LIMITS["max_input_bytes"]:
            raise SnapshotError(
                "max_input_bytes",
                "The input exceeds the fixed byte limit.",
            )

        with tempfile.TemporaryDirectory(prefix="reproready-check-") as temp_root:
            snapshot_path = Path(temp_root) / "source.snapshot"
            digest = hashlib.sha256()
            copied = 0
            input_limit_reached = False

            try:
                with snapshot_path.open("xb") as output:
                    while chunk := os.read(descriptor, _COPY_CHUNK_BYTES):
                        if copied + len(chunk) > FIXED_LIMITS["max_input_bytes"]:
                            input_limit_reached = True
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        copied += len(chunk)
            except OSError as exc:
                raise SnapshotError(
                    "snapshot_io_error",
                    "The checker could not complete its source snapshot.",
                ) from exc

            if _checkpoint is not None:
                _checkpoint(_CHECKPOINT_AFTER_SNAPSHOT_COPY)

            after = os.fstat(descriptor)
            if _source_state(before) != _source_state(
                after
            ) or not _source_still_matches(path, after):
                raise SnapshotError(
                    "source_changed",
                    "The source identity or modification state changed while it was copied.",
                )
            if input_limit_reached:
                raise SnapshotError(
                    "max_input_bytes",
                    "The input exceeds the fixed byte limit.",
                )
            if copied != after.st_size:
                raise SnapshotError(
                    "source_changed",
                    "The source size changed while it was copied.",
                )

            snapshot_path.chmod(stat.S_IRUSR)
            yield SourceSnapshot(
                display_name=path.name,
                size_bytes=copied,
                sha256=digest.hexdigest(),
                path=snapshot_path,
            )
    finally:
        os.close(descriptor)


def _worker_entry(
    worker: InspectionWorker,
    snapshot_path: str,
    report_path: str,
    progress: Connection,
    max_report_bytes: int,
) -> NoReturn:
    try:

        def checkpoint(name: str) -> None:
            progress.send(name)

        report = worker(Path(snapshot_path), checkpoint)
        if not isinstance(report, bytes):
            os._exit(_WORKER_INTERNAL_EXIT)
        if not report or len(report) > max_report_bytes:
            os._exit(_WORKER_REPORT_LIMIT_EXIT)

        destination = Path(report_path)
        temporary = destination.with_suffix(".partial")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(report)
        os.replace(temporary, destination)
    except Exception:  # noqa: BLE001 - this boundary must suppress child tracebacks.
        os._exit(_WORKER_INTERNAL_EXIT)
    finally:
        progress.close()
    os._exit(0)


class _RusageInfoV2(ctypes.Structure):
    _fields_ = [
        ("uuid", ctypes.c_ubyte * 16),
        ("user_time", ctypes.c_uint64),
        ("system_time", ctypes.c_uint64),
        ("package_idle_wakeups", ctypes.c_uint64),
        ("interrupt_wakeups", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("wired_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("physical_footprint", ctypes.c_uint64),
        ("process_start_abstime", ctypes.c_uint64),
        ("process_exit_abstime", ctypes.c_uint64),
        ("child_user_time", ctypes.c_uint64),
        ("child_system_time", ctypes.c_uint64),
        ("child_package_idle_wakeups", ctypes.c_uint64),
        ("child_interrupt_wakeups", ctypes.c_uint64),
        ("child_pageins", ctypes.c_uint64),
        ("child_elapsed_abstime", ctypes.c_uint64),
        ("diskio_bytes_read", ctypes.c_uint64),
        ("diskio_bytes_written", ctypes.c_uint64),
    ]


_LIBPROC: ctypes.CDLL | None = None


def _macos_resident_bytes(process_id: int) -> int | None:
    global _LIBPROC
    if _LIBPROC is None:
        try:
            _LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        except OSError:
            return None
        _LIBPROC.proc_pid_rusage.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        _LIBPROC.proc_pid_rusage.restype = ctypes.c_int

    usage = _RusageInfoV2()
    if _LIBPROC.proc_pid_rusage(process_id, 2, ctypes.byref(usage)) != 0:
        return None
    return int(usage.resident_size)


def _linux_resident_bytes(process_id: int) -> int | None:
    try:
        fields = Path(f"/proc/{process_id}/statm").read_text(encoding="ascii").split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (IndexError, OSError, ValueError):
        return None


def _resident_bytes(process_id: int) -> int | None:
    if sys.platform == "darwin":
        return _macos_resident_bytes(process_id)
    if sys.platform == "linux":
        return _linux_resident_bytes(process_id)
    return None


def _drain_checkpoints(progress: Connection, last: str | None) -> str | None:
    try:
        while progress.poll():
            value = progress.recv()
            if isinstance(value, str):
                last = value
    except (EOFError, OSError):
        pass
    return last


def _stop_worker(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.kill()
    process.join()


def run_inspection_child(
    snapshot: SourceSnapshot,
    worker: InspectionWorker,
    *,
    _max_elapsed_seconds: float | None = None,
    _max_worker_rss_bytes: int | None = None,
) -> WorkerOutcome:
    """Run one controlled inspector and return its bounded report bytes or failure."""

    elapsed_limit = (
        float(FIXED_LIMITS["max_elapsed_seconds"])
        if _max_elapsed_seconds is None
        else _max_elapsed_seconds
    )
    memory_limit = (
        FIXED_LIMITS["max_worker_rss_bytes"]
        if _max_worker_rss_bytes is None
        else _max_worker_rss_bytes
    )
    if elapsed_limit <= 0 or memory_limit <= 0:
        raise ValueError("internal worker limits must be positive")

    context = multiprocessing.get_context("spawn")
    receive_progress, send_progress = context.Pipe(duplex=False)
    report_path = snapshot.path.with_name("inspection-report.json")
    process = context.Process(
        name="reproready-inspector",
        target=_worker_entry,
        args=(
            worker,
            str(snapshot.path),
            str(report_path),
            send_progress,
            FIXED_LIMITS["max_report_bytes"],
        ),
    )

    try:
        process.start()
    except Exception:  # noqa: BLE001 - startup failures become bounded outcomes.
        receive_progress.close()
        send_progress.close()
        return WorkerOutcome("error", "worker_start_error", None, None)
    send_progress.close()

    deadline = time.monotonic() + elapsed_limit
    last_checkpoint: str | None = None
    monitor_misses = 0
    stopped_issue: str | None = None
    reached_limit: str | None = None

    while process.is_alive():
        last_checkpoint = _drain_checkpoints(receive_progress, last_checkpoint)
        process_id = process.pid
        resident = _resident_bytes(process_id) if process_id is not None else None
        if resident is None:
            monitor_misses += 1
            if monitor_misses >= _MAX_MONITOR_MISSES:
                stopped_issue = "worker_memory_monitor_error"
                break
        else:
            monitor_misses = 0
            if resident >= memory_limit:
                stopped_issue = "worker_memory_limit"
                reached_limit = "max_worker_rss_bytes"
                break

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stopped_issue = "worker_timeout"
            reached_limit = "max_elapsed_seconds"
            break
        process.join(min(_MONITOR_INTERVAL_SECONDS, remaining))

    if stopped_issue is not None:
        _stop_worker(process)
        last_checkpoint = _drain_checkpoints(receive_progress, last_checkpoint)
        receive_progress.close()
        process.close()
        return WorkerOutcome(
            "error",
            stopped_issue,
            reached_limit,
            last_checkpoint,
        )

    process.join()
    last_checkpoint = _drain_checkpoints(receive_progress, last_checkpoint)
    receive_progress.close()
    exit_code = process.exitcode
    process.close()

    if exit_code == _WORKER_REPORT_LIMIT_EXIT:
        return WorkerOutcome(
            "error",
            "worker_report_limit",
            "max_report_bytes",
            last_checkpoint,
        )
    if exit_code != 0:
        return WorkerOutcome("error", "worker_error", None, last_checkpoint)

    try:
        with report_path.open("rb") as report_file:
            report = report_file.read(FIXED_LIMITS["max_report_bytes"] + 1)
    except OSError:
        return WorkerOutcome("error", "worker_result_error", None, last_checkpoint)
    if not report:
        return WorkerOutcome("error", "worker_result_error", None, last_checkpoint)
    if len(report) > FIXED_LIMITS["max_report_bytes"]:
        return WorkerOutcome(
            "error",
            "worker_report_limit",
            "max_report_bytes",
            last_checkpoint,
        )
    try:
        decoded = json.loads(report)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return WorkerOutcome("error", "worker_result_error", None, last_checkpoint)
    if not isinstance(decoded, dict):
        return WorkerOutcome("error", "worker_result_error", None, last_checkpoint)
    return WorkerOutcome("complete", None, None, last_checkpoint, report)

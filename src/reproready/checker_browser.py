"""Private bounded-member browser over one checker snapshot and worker."""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing.connection import Connection
from pathlib import Path
from typing import NoReturn

from .checker_intake import (
    FIXED_LIMITS,
    SourceSnapshot,
    _resident_bytes,
    _stop_worker,
    snapshot_source,
)
from .checker_inventory import InspectionEngine, InventoryError
from .checker_report import encode_report

BROWSER_VERSION = "pilot-v1"
BROWSER_LIMITS = {
    "max_browser_operations": 128,
    "max_browser_active_seconds": 120,
    "max_browser_session_response_bytes": 8 * 1024**2,
    "max_browser_response_bytes": 256 * 1024,
    "max_list_records": 200,
    "max_search_query_codepoints": 120,
    "max_search_matches": 100,
    "max_read_lines": 200,
    "max_read_bytes": 64 * 1024,
    "max_browser_display_codepoints": 240,
}
_MONITOR_INTERVAL_SECONDS = 0.01
_MAX_MONITOR_MISSES = 3
_MEMBER_ID = re.compile(r"member:[0-9]+")


class BrowserError(RuntimeError):
    """Base class for fixed, non-artifact browser failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BrowserProtocolError(BrowserError):
    """A browser request violates the fixed request contract."""


class BrowserStartupError(BrowserError):
    """The bounded browser could not produce a completed inventory."""


class BrowserSessionClosed(BrowserError):
    """A request arrived after browser capabilities were revoked."""


def _write_private(path: Path, body: bytes) -> None:
    temporary = path.with_suffix(".partial")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        stat.S_IRUSR | stat.S_IWUSR,
    )
    with os.fdopen(descriptor, "wb") as output:
        output.write(body)
    os.replace(temporary, path)
    path.chmod(stat.S_IRUSR)


def _browser_worker(
    snapshot: SourceSnapshot,
    report_path: str,
    cache_root: str,
    connection: Connection,
) -> NoReturn:
    try:
        cache = Path(cache_root)
        cache.mkdir(mode=0o700)
        engine = InspectionEngine(snapshot, connection.send, cache)
        report = engine.inspect()
        _write_private(Path(report_path), encode_report(report))
        connection.send(
            {
                "kind": "ready",
                "detected_kind": engine.detected_kind,
                "target_ids": [target.target_id for target in engine.targets],
            }
        )
        while True:
            command = connection.recv()
            if not isinstance(command, dict):
                raise InventoryError(
                    "invalid_request", "The browser request is invalid."
                )
            if command.get("kind") == "close":
                break
            if command.get("kind") != "request" or not isinstance(
                command.get("request"), dict
            ):
                raise InventoryError(
                    "invalid_request", "The browser request is invalid."
                )
            response = engine.handle_browser_request(command["request"])
            connection.send({"kind": "response", "response": response})
    except (EOFError, BrokenPipeError):
        pass
    except InventoryError as error:
        try:
            connection.send(
                {"kind": "fatal", "code": error.code, "message": error.message}
            )
        except (EOFError, BrokenPipeError, OSError):
            pass
    except Exception:  # noqa: BLE001 - child tracebacks and artifact text stay private.
        try:
            connection.send(
                {
                    "kind": "fatal",
                    "code": "worker_error",
                    "message": "The inspection worker stopped unexpectedly.",
                }
            )
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()
    os._exit(0)


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _cursor(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, str) and len(value) <= 256 and value.isascii() and bool(value)
    )


def _exact_keys(request: dict[str, object], keys: set[str]) -> None:
    if set(request) != keys:
        raise BrowserProtocolError("invalid_request", "The browser request is invalid.")


def _validate_request(
    request: dict[str, object], artifact_sha256: str
) -> dict[str, object]:
    common = {"browser_version", "artifact_sha256", "operation"}
    if request.get("browser_version") != BROWSER_VERSION:
        raise BrowserProtocolError(
            "browser_version_mismatch", "The browser version does not match."
        )
    if request.get("artifact_sha256") != artifact_sha256:
        raise BrowserProtocolError(
            "artifact_digest_mismatch", "The artifact digest does not match."
        )
    operation = request.get("operation")
    if operation == "list_members":
        _exact_keys(request, common | {"cursor", "limit"})
        if not _cursor(request["cursor"]) or not _integer(
            request["limit"], 1, BROWSER_LIMITS["max_list_records"]
        ):
            raise BrowserProtocolError(
                "invalid_request", "The browser request is invalid."
            )
    elif operation == "search_members":
        _exact_keys(request, common | {"target_id", "query", "cursor", "limit"})
        target_id = request["target_id"]
        query = request["query"]
        if not (
            isinstance(target_id, str)
            and (target_id in {"all", "artifact"} or _MEMBER_ID.fullmatch(target_id))
            and isinstance(query, str)
            and 1 <= len(query) <= BROWSER_LIMITS["max_search_query_codepoints"]
            and _cursor(request["cursor"])
            and _integer(request["limit"], 1, BROWSER_LIMITS["max_search_matches"])
            and not any(0xD800 <= ord(character) <= 0xDFFF for character in query)
        ):
            raise BrowserProtocolError(
                "invalid_request", "The browser request is invalid."
            )
    elif operation == "read_member":
        _exact_keys(request, common | {"target_id", "start_line", "max_lines"})
        target_id = request["target_id"]
        if not (
            isinstance(target_id, str)
            and (target_id == "artifact" or _MEMBER_ID.fullmatch(target_id))
            and _integer(request["start_line"], 1, 2**63 - 1)
            and _integer(request["max_lines"], 1, BROWSER_LIMITS["max_read_lines"])
        ):
            raise BrowserProtocolError(
                "invalid_request", "The browser request is invalid."
            )
    else:
        raise BrowserProtocolError("invalid_request", "The browser request is invalid.")
    return request


def parse_request(raw: bytes, artifact_sha256: str) -> dict[str, object]:
    """Load one strict request object for a browser bound to one digest."""

    if len(raw) > BROWSER_LIMITS["max_browser_response_bytes"]:
        raise BrowserProtocolError("invalid_request", "The browser request is invalid.")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise BrowserProtocolError(
                    "invalid_request", "The browser request is invalid."
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise BrowserProtocolError("invalid_request", "The browser request is invalid.")

    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise BrowserProtocolError(
            "invalid_request", "The browser request is invalid."
        ) from error
    if not isinstance(value, dict):
        raise BrowserProtocolError("invalid_request", "The browser request is invalid.")
    return _validate_request(value, artifact_sha256)


class MemberBrowserSession:
    """Parent-side monitor for one long-lived bounded inspection worker."""

    def __init__(self, snapshot: SourceSnapshot) -> None:
        self.snapshot = snapshot
        self._process: multiprocessing.Process | None = None
        self._connection: Connection | None = None
        self._report: dict[str, object] | None = None
        self._target_ids: tuple[str, ...] = ()
        self._operation_count = 0
        self._active_seconds = 0.0
        self._response_bytes = 0
        self._closed = False

    @property
    def report(self) -> dict[str, object]:
        if self._report is None:
            raise BrowserStartupError(
                "browser_not_started", "The browser session has not started."
            )
        return self._report

    @property
    def target_ids(self) -> tuple[str, ...]:
        return self._target_ids

    def start(self) -> None:
        if self._process is not None:
            raise BrowserStartupError(
                "browser_already_started", "The browser session is already started."
            )
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        report_path = self.snapshot.path.with_name("browser-intake-report.json")
        cache_root = self.snapshot.path.with_name("browser-cache")
        process = context.Process(
            name="reproready-browser",
            target=_browser_worker,
            args=(self.snapshot, str(report_path), str(cache_root), child),
        )
        try:
            process.start()
        except Exception as error:
            parent.close()
            child.close()
            raise BrowserStartupError(
                "worker_start_error", "The inspection worker could not start."
            ) from error
        child.close()
        self._process = process
        self._connection = parent
        try:
            message = self._wait_for_message(float(FIXED_LIMITS["max_elapsed_seconds"]))
        except BrowserStartupError:
            self.close()
            raise
        if message.get("kind") == "fatal":
            self.close()
            raise BrowserStartupError(str(message["code"]), str(message["message"]))
        if message.get("kind") != "ready":
            self.close()
            raise BrowserStartupError(
                "worker_error", "The inspection worker did not become ready."
            )
        try:
            with report_path.open("rb") as handle:
                body = handle.read(FIXED_LIMITS["max_report_bytes"] + 1)
            report = json.loads(body)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self.close()
            raise BrowserStartupError(
                "worker_result_error", "The inspection report could not be read."
            ) from error
        if len(body) > FIXED_LIMITS["max_report_bytes"] or not isinstance(report, dict):
            self.close()
            raise BrowserStartupError(
                "worker_result_error", "The inspection report could not be read."
            )
        targets = message.get("target_ids")
        if not isinstance(targets, list) or any(
            not isinstance(item, str) for item in targets
        ):
            self.close()
            raise BrowserStartupError(
                "worker_result_error", "The inspection report could not be read."
            )
        self._report = report
        self._target_ids = tuple(targets)

    def _wait_for_message(self, timeout: float) -> dict[str, object]:
        process = self._process
        connection = self._connection
        if process is None or connection is None:
            raise BrowserStartupError(
                "browser_not_started", "The browser session has not started."
            )
        deadline = time.monotonic() + timeout
        misses = 0
        while True:
            if connection.poll(_MONITOR_INTERVAL_SECONDS):
                try:
                    message = connection.recv()
                except (EOFError, OSError) as error:
                    raise BrowserStartupError(
                        "worker_error", "The inspection worker stopped unexpectedly."
                    ) from error
                if isinstance(message, str):
                    continue
                if isinstance(message, dict):
                    return message
                raise BrowserStartupError(
                    "worker_error", "The inspection worker returned an invalid message."
                )
            if not process.is_alive():
                raise BrowserStartupError(
                    "worker_error", "The inspection worker stopped unexpectedly."
                )
            process_id = process.pid
            resident = _resident_bytes(process_id) if process_id is not None else None
            if resident is None:
                misses += 1
                if misses >= _MAX_MONITOR_MISSES:
                    _stop_worker(process)
                    raise BrowserStartupError(
                        "worker_memory_monitor_error",
                        "The inspection worker memory monitor failed.",
                    )
            else:
                misses = 0
                if resident >= FIXED_LIMITS["max_worker_rss_bytes"]:
                    _stop_worker(process)
                    raise BrowserStartupError(
                        "worker_memory_limit",
                        "The inspection worker reached its resident-memory limit.",
                    )
            if time.monotonic() >= deadline:
                _stop_worker(process)
                raise BrowserStartupError(
                    "worker_timeout", "The inspection worker reached its time limit."
                )

    def request(self, request: dict[str, object]) -> dict[str, object]:
        """Charge and execute one browser request, closing on resource ceilings."""

        if self._closed:
            raise BrowserSessionClosed(
                "session_closed", "The bounded browser session is closed."
            )
        started = time.monotonic()
        self._operation_count += 1
        try:
            validated = _validate_request(dict(request), self.snapshot.sha256)
        except BrowserProtocolError:
            self._active_seconds += time.monotonic() - started
            self._response_bytes += len(b'{"error":"invalid_request"}')
            if (
                self._operation_count >= BROWSER_LIMITS["max_browser_operations"]
                or self._active_seconds >= BROWSER_LIMITS["max_browser_active_seconds"]
                or self._response_bytes
                >= BROWSER_LIMITS["max_browser_session_response_bytes"] - 64 * 1024
            ):
                self.close()
            raise
        if self._operation_count >= BROWSER_LIMITS["max_browser_operations"]:
            return self._terminal_response(validated, "max_browser_operations", started)
        remaining = BROWSER_LIMITS["max_browser_active_seconds"] - self._active_seconds
        if remaining <= 0:
            return self._terminal_response(
                validated, "max_browser_active_seconds", started
            )
        assert self._connection is not None
        try:
            self._connection.send({"kind": "request", "request": validated})
            message = self._wait_for_message(remaining)
        except BrowserStartupError as error:
            if error.code == "worker_timeout":
                return self._terminal_response(
                    validated, "max_browser_active_seconds", started
                )
            if error.code == "worker_memory_limit":
                return self._terminal_response(
                    validated, "max_worker_rss_bytes", started
                )
            self.close()
            raise
        except (BrokenPipeError, EOFError, OSError) as error:
            self.close()
            raise BrowserStartupError(
                "worker_error", "The inspection worker stopped unexpectedly."
            ) from error
        self._active_seconds += time.monotonic() - started
        if self._active_seconds >= BROWSER_LIMITS["max_browser_active_seconds"]:
            return self._terminal_response(
                validated, "max_browser_active_seconds", None
            )
        if message.get("kind") == "fatal":
            self.close()
            raise BrowserStartupError(str(message["code"]), str(message["message"]))
        response = message.get("response")
        if message.get("kind") != "response" or not isinstance(response, dict):
            self.close()
            raise BrowserStartupError(
                "worker_error", "The inspection worker returned an invalid response."
            )
        encoded = json.dumps(
            response,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > BROWSER_LIMITS["max_browser_response_bytes"]:
            return self._terminal_response(
                validated, "max_browser_response_bytes", None
            )
        if (
            self._response_bytes + len(encoded)
            > BROWSER_LIMITS["max_browser_session_response_bytes"] - 64 * 1024
        ):
            return self._terminal_response(
                validated, "max_browser_session_response_bytes", None
            )
        self._response_bytes += len(encoded)
        return response

    def _terminal_response(
        self,
        request: dict[str, object],
        limit: str,
        started: float | None,
    ) -> dict[str, object]:
        if started is not None:
            self._active_seconds += time.monotonic() - started
        operation = str(request["operation"])
        response: dict[str, object] = {
            "status": "partial",
            "reached_limits": self._report_reached_limits() + [limit],
            "issues": [
                {
                    "code": "resource_limit_reached",
                    "target_id": None,
                    "message": "A fixed browser resource limit closed the session.",
                }
            ],
            "browser_version": BROWSER_VERSION,
            "artifact_sha256": self.snapshot.sha256,
            "operation": operation,
        }
        if operation == "list_members":
            response.update(
                {"targets": [], "next_cursor": None, "inventory_complete": False}
            )
        elif operation == "search_members":
            response.update(
                {
                    "matches": [],
                    "next_cursor": None,
                    "search_complete": False,
                    "searched_target_count": 0,
                    "skipped_targets": [],
                    "searched_bytes": 0,
                }
            )
        else:
            response.update(
                {
                    "start_line": int(request["start_line"]),
                    "end_line": None,
                    "text": "",
                    "next_line": None,
                    "end_of_text": False,
                    "text_truncated": False,
                    "returned_bytes": 0,
                }
            )
        self._response_bytes += len(
            json.dumps(response, separators=(",", ":")).encode("utf-8")
        )
        self.close()
        return response

    def _report_reached_limits(self) -> list[str]:
        if self._report is None:
            return []
        limits = self._report.get("limits")
        if not isinstance(limits, dict) or not isinstance(limits.get("reached"), list):
            return []
        return [str(item) for item in limits["reached"]]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        connection = self._connection
        if connection is not None:
            try:
                if process is not None and process.is_alive():
                    connection.send({"kind": "close"})
            except (BrokenPipeError, EOFError, OSError):
                pass
            connection.close()
        if process is not None:
            process.join(2)
            if process.is_alive():
                _stop_worker(process)
            process.close()
        self._process = None
        self._connection = None


@contextmanager
def open_member_browser(
    source: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
) -> Iterator[MemberBrowserSession]:
    """Snapshot one regular file and hold its bounded member browser open."""

    with snapshot_source(source) as snapshot:
        if expected_sha256 is not None and snapshot.sha256 != expected_sha256:
            raise BrowserStartupError(
                "artifact_digest_mismatch",
                "The artifact digest does not match the expected source identity.",
            )
        session = MemberBrowserSession(snapshot)
        session.start()
        try:
            yield session
        finally:
            session.close()

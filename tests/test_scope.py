"""Tests for scope: the X-gate, default-in I, README discovery."""

from __future__ import annotations

from reproready.inventory import RawEntry
from reproready.scope import compute_scope, pick_readme


def _f(path: str, is_dir: bool = False, size: int = 10) -> RawEntry:
    return RawEntry(path=path, size=size, is_dir=is_dir, depth=path.count("/") + 1)


def test_execution_gate_two_units() -> None:
    # One runnable unit → Execution out of scope.
    one = compute_scope([_f("solver.py"), _f("README.md")])
    assert one.in_scope == {"E": True, "I": True, "X": False, "V": True}
    assert one.n_runnable == 1
    # Two runnable units → Execution in scope.
    two = compute_scope([_f("model.py"), _f("train.py")])
    assert two.in_scope["X"] is True
    assert two.stages == ["E", "I", "X", "V"]


def test_inputs_default_in() -> None:
    s = compute_scope([_f("solver.py")])
    assert s.in_scope["I"] is True  # default-in (import-scan parked)


def test_readme_discovery_shallowest() -> None:
    entries = [_f("sub/README.md"), _f("README.md"), _f("model.py")]
    path, has, dark = pick_readme(entries)
    assert (path, has, dark) == ("README.md", True, False)


def test_readme_dark_when_unparseable() -> None:
    path, has, dark = pick_readme([_f("README.pdf"), _f("model.py")])
    assert has is True and dark is True and path == "README.pdf"
    s = compute_scope([_f("README.pdf"), _f("model.py")])
    assert s.readme_dark == 1


def test_no_readme() -> None:
    path, has, dark = pick_readme([_f("model.py")])
    assert (path, has, dark) == ("", False, False)


def test_junk_runnable_not_counted() -> None:
    s = compute_scope([_f("__pycache__/x.py"), _f("model.py")])
    assert s.n_runnable == 1

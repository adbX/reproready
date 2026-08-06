"""End-to-end test over the bundled demo artifact.

Scores ``examples/demo-artifact`` both as a directory and as a zip, and pins the
full graded grid, the per-stage vector, ``R``, and the tier. The demo is a
well-formed artifact, so this doubles as a golden regression on the whole
pipeline: any change to routing, scope, the rubric, or aggregation that moves
the demo's score shows up here.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from reproready import score_path
from reproready.cli import report_to_dict

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-artifact"

EXPECTED_GRID = {
    ("E", "implementation"): 1.0,
    ("E", "documentation"): 0.5,
    ("I", "implementation"): 1.0,
    ("I", "documentation"): 0.5,
    ("X", "implementation"): 1.0,
    ("X", "documentation"): 0.5,
    ("V", "implementation"): 0.5,  # producer present at the deterministic floor
    ("V", "documentation"): 0.5,
}
EXPECTED_VECTOR = {"E": 1.0, "I": 1.0, "X": 1.0, "V": 0.75}
EXPECTED_R = 0.75**0.25  # geomean(1, 1, 1, 0.75) ≈ 0.9306


def _assert_report(r) -> None:
    assert r.has_code is True
    assert r.in_scope == {"E": True, "I": True, "X": True, "V": True}
    assert r.readme_path == "README.md" and not r.readme_dark
    assert r.cells_flat == pytest.approx(EXPECTED_GRID)
    assert r.vector == pytest.approx(EXPECTED_VECTOR)
    assert r.r == pytest.approx(EXPECTED_R)
    assert r.tier == "3"
    assert r.coverage == 4
    assert r.pointers["V"] == ["plot_fig2.py"]


def test_demo_dir() -> None:
    report = score_path(DEMO)
    assert report.archive_kind == "dir"
    _assert_report(report)


def test_demo_zip(tmp_path: Path) -> None:
    z = tmp_path / "demo.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(DEMO.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(DEMO).as_posix())
    report = score_path(z)
    assert report.archive_kind == "zip"
    _assert_report(report)


def test_demo_json_roundtrips() -> None:
    payload = report_to_dict(score_path(DEMO))
    # The JSON view survives a serialise/parse round-trip and preserves R.
    parsed = json.loads(json.dumps(payload))
    assert parsed["r"] == pytest.approx(EXPECTED_R)
    assert parsed["tier"] == "3"
    assert len(parsed["grid"]) == 8

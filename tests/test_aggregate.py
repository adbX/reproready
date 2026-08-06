"""Tests for the config-driven aggregation.

Pins the noisy-OR / geometric-mean math, the hard-zero floor, the tier +
redundancy gate, the two worked examples as golden tests, and the modularity
property: a different :class:`AggregationConfig` changes ``R`` from the *same*
graded cells.
"""

from __future__ import annotations

import pytest

from reproready.aggregate import (
    TILTED_WEIGHTS,
    AggregationConfig,
    aggregate,
    noisy_or,
)

ALL_IN = {"E": True, "I": True, "X": True, "V": True}


def test_noisy_or_and_documentation_cap() -> None:
    assert noisy_or(1.0, 0.0) == pytest.approx(1.0)
    assert noisy_or(0.5, 0.5) == pytest.approx(0.75)  # 1 - 0.5*0.5
    assert noisy_or(0.0, 1.0) == pytest.approx(0.5)  # Docs capped at 0.5
    assert noisy_or(0.0, 1.0, documentation_cap=0.3) == pytest.approx(0.3)


def test_geometric_mean_calibrated() -> None:
    # Every stage v → R = v.
    cells = {(s, "implementation"): 0.7 for s in ("E", "I", "X", "V")}
    assert aggregate(cells, ALL_IN, True, False).r == pytest.approx(0.7)


def test_hard_zero_floor() -> None:
    cells = {
        ("E", "implementation"): 1.0,
        ("I", "implementation"): 0.0,
        ("X", "implementation"): 1.0,
        ("V", "implementation"): 1.0,
    }
    out = aggregate(cells, ALL_IN, True, False)
    assert out.r == 0.0 and out.tier == "0"


def test_scope_renormalises() -> None:
    # X out of scope → three stages renormalise; equal values → that value.
    cells = {(s, "implementation"): 0.5 for s in ("E", "I", "V")}
    scope = {"E": True, "I": True, "X": False, "V": True}
    assert aggregate(cells, scope, True, False).r == pytest.approx(0.5)


# --- worked examples (golden) ------------------------------------------------


def test_worked_example_a() -> None:
    # Mid-quality archive: vector (.75, .75, .75, .5) → R ≈ 0.68, Tier 2.
    cells = {
        ("E", "implementation"): 0.5,
        ("E", "documentation"): 0.5,
        ("I", "implementation"): 0.5,
        ("I", "documentation"): 0.5,
        ("X", "implementation"): 0.5,
        ("X", "documentation"): 0.5,
        ("V", "implementation"): 0.5,
        ("V", "documentation"): 0.0,
    }
    out = aggregate(cells, ALL_IN, True, False)
    assert out.vector == pytest.approx({"E": 0.75, "I": 0.75, "X": 0.75, "V": 0.5})
    assert out.r == pytest.approx(0.68, abs=5e-3)
    assert out.tier == "2"
    assert out.coverage == 4


def test_worked_example_b() -> None:
    # Lone self-contained script: S = {E, I, V}, vector (0.5, 0, —, 0.5) → R = 0.
    cells = {
        ("E", "implementation"): 0.0,
        ("E", "documentation"): 0.5,
        ("I", "implementation"): 0.0,
        ("I", "documentation"): 0.0,
        ("V", "implementation"): 0.5,
        ("V", "documentation"): 0.0,
    }
    scope = {"E": True, "I": True, "X": False, "V": True}
    out = aggregate(cells, scope, True, False)
    assert out.vector["E"] == pytest.approx(0.5)
    assert out.vector["I"] == pytest.approx(0.0)
    assert out.vector["V"] == pytest.approx(0.5)
    assert "X" not in out.vector
    assert out.r == 0.0 and out.tier == "0"


# --- tiers + redundancy ------------------------------------------------------


def test_tier_robust_needs_redundancy() -> None:
    # Strong + 2 redundant stages → Tier 3.
    redundant = {
        ("E", "implementation"): 1.0,
        ("E", "documentation"): 0.5,
        ("I", "implementation"): 1.0,
        ("I", "documentation"): 0.5,
        ("X", "implementation"): 1.0,
        ("V", "implementation"): 1.0,
    }
    assert aggregate(redundant, ALL_IN, True, False).tier == "3"
    # Same strength, no redundancy → caps at Solid (Tier 2).
    single = {(s, "implementation"): 1.0 for s in ("E", "I", "X", "V")}
    assert aggregate(single, ALL_IN, True, False).tier == "2"


def test_tier_na_without_code() -> None:
    assert aggregate({}, ALL_IN, has_code=False, readme_dark=False).tier == "N/A"


def test_coverage_readme_dark() -> None:
    # README dark: stages with Code signal stay observable; pure-Docs drop.
    cells = {("E", "implementation"): 0.5}
    out = aggregate(cells, ALL_IN, True, readme_dark=True)
    assert out.coverage == 1
    assert set(out.vector) == {"E"}  # I/X/V unobservable → dropped
    assert out.r == pytest.approx(0.5)  # not floored to 0 by the drops
    # Parseable README → all four observable.
    assert aggregate(cells, ALL_IN, True, readme_dark=False).coverage == 4


# --- modularity: same cells, different config → different R ------------------

_SWEEP_CELLS = {
    ("E", "implementation"): 1.0,
    ("I", "implementation"): 1.0,
    ("X", "implementation"): 1.0,
    ("V", "implementation"): 0.5,
}


def test_config_sweep_changes_r_from_same_cells() -> None:
    geomean = aggregate(_SWEEP_CELLS, ALL_IN, True, False, AggregationConfig())
    minimum = aggregate(
        _SWEEP_CELLS, ALL_IN, True, False, AggregationConfig(across_stage="min")
    )
    arithmetic = aggregate(
        _SWEEP_CELLS, ALL_IN, True, False, AggregationConfig(across_stage="arithmetic")
    )
    tilted = aggregate(
        _SWEEP_CELLS,
        ALL_IN,
        True,
        False,
        AggregationConfig(stage_weights=dict(TILTED_WEIGHTS)),
    )
    leave_v = aggregate(
        _SWEEP_CELLS, ALL_IN, True, False, AggregationConfig(leave_out="V")
    )

    assert geomean.r == pytest.approx((1 * 1 * 1 * 0.5) ** 0.25)  # ≈ 0.841
    assert minimum.r == pytest.approx(0.5)
    assert arithmetic.r == pytest.approx(0.875)
    assert leave_v.r == pytest.approx(1.0)  # V dropped
    # Tilting weight onto the weak V stage pulls geomean below equal weights.
    assert tilted.r < geomean.r
    # Four distinct aggregators over one stored grid.
    assert len({round(x.r, 4) for x in (geomean, minimum, arithmetic, leave_v)}) == 4

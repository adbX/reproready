"""Aggregation: cell grid to stage vector, ReproReady score, tier, and coverage.

A **pure** function over the graded cells, parameterised by an
:class:`AggregationConfig`. This is the core modularity: every scoring variant —
geometric mean vs. min, equal vs. tilted weights, leave-one-stage-out, a
documentation-cap sweep — is just a different config over the *same* graded
cells, so a sweep never re-reads archives or re-calls the model.

The class defaults reproduce the baseline calculation:

- **within a stage** — noisy-OR ``R_s = 1 − (1−Implementation)(1−Documentation)``,
  Documentation capped at ``0.5``.
- **across stages** — equal-weight geometric mean ``(∏ R_s)^{1/n}`` over the
  in-scope, observable stages, with a hard ``R_s = 0 ⇒ R = 0`` floor.
- **tier** — baseline score bands with a redundancy gate.
- **coverage** — count of in-scope stages visible to a static read.

Public ``score_path`` uses :data:`PROMOTED_CONFIG`, which replaces the baseline
tier bands and enables false-zero scope handling.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from . import scope as scope_mod
from .routing import DOCUMENTATION, IMPLEMENTATION, STAGES

DOCUMENTATION_CAP = 0.5

# Equal weights; the 1/n exponent renormalises over scope.
EQUAL_WEIGHTS: dict[str, float] = {s: 0.25 for s in STAGES}
# An illustrative tilt that emphasises the harder Execution/Validation end. Not
# the primary score; reported alongside it.
TILTED_WEIGHTS: dict[str, float] = {"E": 0.15, "I": 0.15, "X": 0.35, "V": 0.35}


def noisy_or(
    implementation: float,
    documentation: float,
    documentation_cap: float = DOCUMENTATION_CAP,
) -> float:
    """RBD parallel-block / noisy-OR within one stage."""
    d = min(documentation, documentation_cap)
    return 1.0 - (1.0 - implementation) * (1.0 - d)


# Default tier re-cut band edges within ``[0.5, 1]`` (T1 ``[0.5, b1)`` / T2
# ``[b1, b2)`` / T3 ``[b2, 1]``). Tunable; the *baseline* tier scheme ignores them.
TIER_RECUT_CUTS: tuple[float, float] = (0.66, 0.80)


@dataclass(frozen=True)
class AggregationConfig:
    """Every aggregation choice, defaulting to the baseline mapping.

    The defaults reproduce the version-stamped baseline exactly. The extra
    fields are the tiers-&-scope knobs plus the *non-promotable*
    floor-softening diagnostics — all inert at their defaults, so a default
    config reproduces the original baseline scorer.
    """

    within_stage: Callable[[float, float, float], float] = noisy_or
    across_stage: str = "geomean"  # geomean | min | arithmetic
    stage_weights: dict[str, float] = field(default_factory=lambda: dict(EQUAL_WEIGHTS))
    documentation_cap: float = DOCUMENTATION_CAP
    leave_out: str | None = None  # drop a stage (leave-one-out arm)
    # Tier mapping. 'baseline' = the version-stamped cut (Tier 1 lands in the
    # structurally-empty (0, 0.5) dead zone); 'recut' re-bands [0.5, 1].
    tier_scheme: str = "baseline"  # baseline | recut
    tier_cuts: tuple[float, float] = TIER_RECUT_CUTS
    # False-zero scope: an in-scope stage with *no static window*
    # (implementation mute AND documentation channel unreadable) resolves to
    # not-in-scope (dropped from the geomean, docks coverage) rather than
    # in-scope-and-scored-0.
    false_zero_scope: bool = False
    false_zero_stages: frozenset[str] = frozenset({"I", "V"})
    # Non-promotable floor-softening diagnostics; never shipped.
    # zero_floor: replace the hard geomean R=0 with a small ε. missing_stage_value:
    # an in-scope stage scoring 0 contributes this instead of 0. Both 0 = baseline.
    zero_floor: float = 0.0
    missing_stage_value: float = 0.0


# ---------------------------------------------------------------------------
# The canonical scored mapping.
# ---------------------------------------------------------------------------
# The promoted tiers-&-scope config: the tier re-cut plus false-zero scope. It
# freezes only the **tier-cut + scope labelling** — the ``R`` *formula* is the
# base formula above and is **unchanged** (so this is not a freeze of the
# metric, only of how the score is banded and scoped). Bump
# ``TIER_SCOPE_VERSION`` if the cut-points or false-zero set ever change.
TIER_SCOPE_VERSION = "tier-scope-v1"

PROMOTED_CONFIG = AggregationConfig(tier_scheme="recut", false_zero_scope=True)


@dataclass(frozen=True)
class Aggregate:
    """One artifact's aggregated readiness."""

    vector: dict[str, float]  # scored stage → R_s
    r: float  # the scalar ReproReady score
    tier: str  # 'N/A' | '0' | '1' | '2' | '3'
    coverage: int  # # in-scope stages visible to a static read
    redundant: dict[str, bool]  # scored stage → both channels ≥ 0.5


def _across(
    values: list[float], weights: list[float], how: str, zero_floor: float = 0.0
) -> float:
    if not values:
        return 0.0
    if how == "min":
        return min(values)
    total_w = sum(weights)
    norm = [w / total_w for w in weights]
    if how == "arithmetic":
        return sum(w * v for w, v in zip(norm, values))
    # geometric mean with an explicit hard-zero floor (no log 0). ``zero_floor``
    # (diagnostic) softens that floor: a zeroed stage reads as ε, not a hard
    # collapse — compensatory, so never promoted.
    vals = [v if v > 0.0 else zero_floor for v in values]
    if any(v <= 0.0 for v in vals):
        return 0.0
    return math.exp(sum(w * math.log(v) for w, v in zip(norm, vals)))


def aggregate(
    cells: dict[tuple[str, str], float],
    in_scope: dict[str, bool],
    has_code: bool,
    readme_dark: bool,
    config: AggregationConfig | None = None,
    *,
    has_readme: bool = True,
) -> Aggregate:
    """Aggregate one artifact from its ``{(stage, channel): grade}`` grid.

    ``cells`` maps every present cell to its grade (absent → 0). A stage that is
    in scope but **unobservable** (README dark and no Implementation signal) is
    dropped from the scored set and only docks coverage; an
    in-scope, observable stage with both channels 0 scores ``R_s = 0`` and
    floors ``R``.

    With ``config.false_zero_scope``, the unobservable rule is widened from
    *README-dark* to *no readable documentation channel* (dark **or** absent) for
    the ``false_zero_stages`` (Inputs / Validation) — a stage with no
    implementation pointer *and* no way to read its documentation is a
    measurement gap, not an absence, so it is scoped out rather than scored 0.
    ``has_readme`` carries the documentation-channel-readability signal this
    needs (ignored at the default config).
    """
    cfg = config or AggregationConfig()

    vector: dict[str, float] = {}
    redundant: dict[str, bool] = {}
    observable = 0
    for s in STAGES:
        if not in_scope.get(s, False):
            continue
        implementation = cells.get((s, IMPLEMENTATION), 0.0)
        documentation = min(cells.get((s, DOCUMENTATION), 0.0), cfg.documentation_cap)
        # Unobservable: README dark and no Implementation signal
        # → drop, dock coverage. false_zero_scope widens this to "no readable
        # documentation channel" for the configured false-zero stages.
        unobservable = readme_dark and implementation <= 0.0
        if cfg.false_zero_scope and s in cfg.false_zero_stages:
            unobservable = unobservable or scope_mod.is_unobservable_zero(
                implementation, documentation, has_readme, readme_dark
            )
        if unobservable:
            continue
        observable += 1
        redundant[s] = implementation >= 0.5 and documentation >= 0.5
        if s == cfg.leave_out:
            continue
        r_s = cfg.within_stage(implementation, documentation, cfg.documentation_cap)
        # Diagnostic: an in-scope stage scoring 0 contributes a small penalty
        # instead of flooring R — compensatory, so never promoted.
        if r_s <= 0.0 and cfg.missing_stage_value > 0.0:
            r_s = cfg.missing_stage_value
        vector[s] = r_s

    weights = [cfg.stage_weights[s] for s in vector]
    r = _across(list(vector.values()), weights, cfg.across_stage, cfg.zero_floor)
    return Aggregate(
        vector=vector,
        r=r,
        tier=tier(r, redundant, vector, has_code, cfg),
        coverage=observable,
        redundant=redundant,
    )


def tier(
    r: float,
    redundant: dict[str, bool],
    vector: dict[str, float],
    has_code: bool,
    config: AggregationConfig | None = None,
) -> str:
    """Return the configured ordinal label from ``R``.

    The ``baseline`` scheme is the version-stamped cut (redundancy-gated Tier 3,
    a ``≥0.5`` Tier 2, and a Tier 1 that lands in the structurally-empty
    ``(0, 0.5)`` band — so it never populates). The ``recut`` scheme re-cuts the
    ordinal bands onto ``R``'s real support ``{0} ∪ [0.5, 1]``: Tier 0 is the
    hard zero, and Tier 1/2/3 sub-band the ``[0.5, 1]`` continuum so all four
    populate. The re-cut is a **pure relabel** — ``R`` is identical either way.
    """
    cfg = config or AggregationConfig()
    if not has_code:
        return "N/A"
    if not vector or r <= 0.0:
        return "0"
    if cfg.tier_scheme == "recut":
        b1, b2 = cfg.tier_cuts
        if r >= b2:
            return "3"
        if r >= b1:
            return "2"
        return "1"
    n_redundant = sum(1 for s in vector if redundant.get(s, False))
    if r >= 0.8 and n_redundant >= 2:
        return "3"
    if r >= 0.5:
        return "2"
    return "1"

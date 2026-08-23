"""Scope decisions made from the file inventory.

Decides, from the file inventory alone (no archive bytes), which of the four
stages an artifact actually exercises, and finds the README that carries the
Documentation channel. Offline and deterministic.

Scope rules:

- **Environment** — always in scope (code always needs an environment).
- **Inputs** — in scope by default; the *confirmed self-contained* exception
  (no external data, no configs) would require an import scan that is not yet
  implemented, so this is **default-in**.
- **Execution** — in scope iff the archive has **≥ 2 runnable units** (scripts,
  notebooks, or computational modules); a one-file artifact has nothing to
  orchestrate beyond running itself.
- **Validation** — always in scope (the paper reports results to check against).

A README that is *present but unparseable* (e.g. only ``README.pdf``) is flagged
``readme_dark``: the Documentation channel cannot be read, which downstream drops
unobservable stages from coverage rather than scoring them as
failures.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import routing
from .inventory import RawEntry

# The false-zero rule targets the two stages a static read most often *cannot
# see* even when readiness is high: Inputs (a named public benchmark lives in
# the paper prose, not the artifact) and Validation (results are checkable but
# there is no machine-readable producer). Environment and Execution have
# reliable static windows, so they are excluded.
FALSE_ZERO_STAGES = frozenset({"I", "V"})


def is_unobservable_zero(
    implementation_grade: float,
    documentation_grade: float,
    has_readme: bool,
    readme_dark: bool,
) -> bool:
    """False-zero predicate: is a stage's ``R_s = 0`` a **false** zero?

    A stage is a *false* zero when the static read had **no window** into it: the
    Implementation channel is mute (no routed pointer ⇒ ``implementation_grade = 0``)
    *and* the Documentation channel is unreadable (no README, or README
    present-but-unparseable). Scoring such a stage 0 asserts "not ready" when the
    truth is "not statically visible" — a measurement gap, not an absence.

    A stage with a routed implementation pointer, a documenting README, or a
    README that is readable-but-silent is a **true** zero (we had a window and it
    was empty), so this returns ``False``. Stage-agnostic and pure; it is applied
    only to :data:`FALSE_ZERO_STAGES`.
    """
    if implementation_grade > 0.0 or documentation_grade > 0.0:
        return False
    documentation_readable = has_readme and not readme_dark
    return not documentation_readable


@dataclass(frozen=True)
class Scope:
    """One artifact's scope decision + README discovery."""

    in_scope: dict[str, bool]  # stage → in-scope flag
    n_runnable: int  # runnable-unit count behind the X-scope gate
    readme_path: str
    has_readme: bool
    readme_dark: int  # README present but unparseable → Documentation dark

    @property
    def stages(self) -> list[str]:
        """In-scope stages in canonical E/I/X/V order."""
        return [s for s in routing.STAGES if self.in_scope[s]]


def pick_readme(entries: list[RawEntry]) -> tuple[str, bool, bool]:
    """Return ``(readme_path, has_readme, readme_dark)``.

    Prefer the shallowest renderable README (ties broken alphabetically). If a
    README exists only in an unparseable form (e.g. ``README.pdf``), flag it
    dark — the Documentation channel cannot be read.
    """
    readmes = [
        e
        for e in entries
        if not e.is_dir
        and not routing.is_junk(e.path)
        and routing.basename(e.path).lower().startswith("readme")
    ]
    if not readmes:
        return "", False, False
    renderable = [e for e in readmes if routing.is_renderable_readme(e.path)]
    if renderable:
        best = min(renderable, key=lambda e: (e.depth, e.path))
        return best.path, True, False
    best = min(readmes, key=lambda e: (e.depth, e.path))
    return best.path, True, True


def count_runnable(entries: list[RawEntry]) -> int:
    """Distinct runnable units (scripts / notebooks / computational modules)."""
    seen: set[str] = set()
    for e in entries:
        if routing.is_runnable_unit(e.path, e.is_dir):
            seen.add(e.path)
    return len(seen)


def compute_scope(entries: list[RawEntry]) -> Scope:
    """Scope an artifact from its inventory alone."""
    n_runnable = count_runnable(entries)
    readme_path, has_readme, readme_dark = pick_readme(entries)
    in_scope = {"E": True, "I": True, "X": n_runnable >= 2, "V": True}
    return Scope(
        in_scope=in_scope,
        n_runnable=n_runnable,
        readme_path=readme_path,
        has_readme=has_readme,
        readme_dark=int(readme_dark),
    )

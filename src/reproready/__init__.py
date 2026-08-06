"""ReproReady — static reproduction-readiness scoring for code artifacts.

Give it a code artifact (a directory, ``.zip``, or ``.tar.gz``) and it grades
how well the artifact equips an independent researcher to regenerate the
reported results — **without running any code**. It measures *readiness*, not
executability: it does not predict whether the code would ultimately run, only
whether the artifact removes the manual effort a reproduction would cost.

The scoring core is 100% standard library. ``rich`` is used only by the CLI and
``anthropic`` only by the optional Validation call (the ``llm`` extra).

Quickstart::

    from reproready import score_path

    report = score_path("path/to/artifact.zip")
    print(report.r, report.tier)

The five version stamps below identify the deterministic logic that produced a
report; bumping any of them changes scores and invalidates cached grades.
"""

from __future__ import annotations

from .aggregate import TIER_SCOPE_VERSION
from .extract import EXTRACT_VERSION
from .prompts import PROMPT_VERSION
from .routing import ROUTING_VERSION
from .rubric import RUBRIC_VERSION
from .score import ArtifactReport, score_path

__version__ = "0.1.0"

__all__ = [
    "score_path",
    "ArtifactReport",
    "ROUTING_VERSION",
    "RUBRIC_VERSION",
    "EXTRACT_VERSION",
    "PROMPT_VERSION",
    "TIER_SCOPE_VERSION",
    "__version__",
]

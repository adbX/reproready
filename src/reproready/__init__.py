"""Static checking and reproduction-readiness scoring for code artifacts.

The checker inspects one regular file without running artifact code and returns
exact static observations, coverage limits, and questions for interpretation::

    from reproready import check_path

    check_report = check_path("path/to/artifact.zip")
    print(check_report.inventory_status)

The separate score accepts a directory, ZIP, or tar-gzip artifact and measures
how well its static contents remove manual reproduction work::

    from reproready import score_path

    score_report = score_path("path/to/artifact.zip")
    print(score_report.r, score_report.tier)

Neither surface predicts executability. The score modules use only the standard
library; the checker adds pinned parsing support. Rich is used by the CLI, and
Anthropic is used only by the score's optional Validation call.
"""

from __future__ import annotations

from .aggregate import TIER_SCOPE_VERSION
from .checker import check_path
from .checker_types import CheckInputError, CheckMember, CheckReport
from .extract import EXTRACT_VERSION
from .prompts import PROMPT_VERSION
from .routing import ROUTING_VERSION
from .rubric import RUBRIC_VERSION
from .score import ArtifactReport, score_path

__version__ = "0.2.0"

__all__ = [
    "check_path",
    "CheckReport",
    "CheckMember",
    "CheckInputError",
    "score_path",
    "ArtifactReport",
    "ROUTING_VERSION",
    "RUBRIC_VERSION",
    "EXTRACT_VERSION",
    "PROMPT_VERSION",
    "TIER_SCOPE_VERSION",
    "__version__",
]

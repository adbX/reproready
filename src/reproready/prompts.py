"""Phase-2 Validation prompt — the single promote-only, manuscript-free call.

A fixed system prompt + few-shots → a structured JSON verdict, with pure helpers
(prompt building, response parsing) so the call can be driven by either a sync
loop (Messages API) or the Batches API.

The judgment is **manuscript-free** (no paper text — only the producer files'
basenames + the output-writing call-sites extracted from them, and the README
results section) and **promote-only** (a ``true`` verdict lifts
Validation/Implementation ``0.5 → 1``; a ``false`` verdict never lowers the
deterministic floor). See spec §3.2.

``PROMPT_VERSION`` is a cache-invalidation key (like ``routing-v2`` /
``rubric-v1``): bump it when the prompt changes and re-validation re-runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from textwrap import dedent
from typing import Any

PROMPT_VERSION = "validate-v1"
VALIDATION_MODEL = "claude-haiku-4-5-20251001"

# The structured verdict is ~30 tokens; the cap is small.
MAX_TOKENS = 256


SYSTEM_PROMPT = dedent("""\
    You judge whether a code artifact plausibly regenerates a SPECIFIC NUMBERED
    RESULT of its paper — a named figure or table — from the files it ships.
    You are NOT given the paper; judge only the internal correspondence between
    the producer files and the README shown below.

    You will receive:
    1. PRODUCERS — files routed as result-producers (eval / plot / analyze
       scripts, table/figure generators, bundled result files), each shown as
       its basename plus the output-writing call-sites extracted from it
       (e.g. savefig('figure2.pdf'), to_csv('table1.csv')).
    2. README — the results / evaluation section of the README, if any.

    Decide: does AT LEAST ONE producer plausibly regenerate a specific numbered
    result (a named "Figure N" / "Table N")? Return JSON only.

    The bar — be strict:
    - PROMOTE when a producer ties to a numbered result: a file named like
      fig2.py / make_table1.py, a call like savefig('figure_2.pdf') or
      to_csv('results/table1.csv'), a bundled results_table1.csv, or a README
      line mapping a script to "Figure 2" / "Table 3".
    - KEEP (do not promote) for generic plotting with no numbered result: a
      plot.py that draws a training/loss curve, a generic evaluate.py printing
      metrics to stdout, tensorboard logs, an unnamed results.csv with no
      figure/table reference. The capability to plot is NOT a specific result.

    When unsure, KEEP — the deterministic floor already credits a producer's
    presence; you only add credit for a specific numbered result.
    """)


FEW_SHOTS: list[dict[str, str]] = [
    {
        "user": dedent("""\
            PRODUCERS:
            - fig2.py
                savefig('figure2.pdf')
            README:
            (none)"""),
        "assistant": '{"promote": true, "result_ref": "Figure 2", "confidence": "high"}',
    },
    {
        "user": dedent("""\
            PRODUCERS:
            - plot.py
                plt.plot(losses)
                plt.savefig('loss_curve.png')
            README:
            ## Results
            Run plot.py to see the training loss curve."""),
        "assistant": '{"promote": false, "result_ref": "none", "confidence": "high"}',
    },
    {
        "user": dedent("""\
            PRODUCERS:
            - make_table1.py
                df.to_csv('results/table1.csv')
            README:
            ## Evaluation
            make_table1.py reproduces Table 1 (main benchmark)."""),
        "assistant": '{"promote": true, "result_ref": "Table 1", "confidence": "high"}',
    },
]


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "promote": {
            "type": "boolean",
            "description": (
                "True iff at least one producer plausibly regenerates a "
                "specific numbered figure or table."
            ),
        },
        "result_ref": {
            "type": "string",
            "description": (
                "The numbered result the verdict rests on, e.g. 'Figure 2' / "
                "'Table 1', or 'none'."
            ),
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "med", "low"],
        },
    },
    "required": ["promote", "confidence"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ValidationContext:
    """Manuscript-free context for one artifact's Validation call."""

    # (basename, extracted output-writing call-site snippet) per producer.
    producers: list[tuple[str, str]]
    # README results/evaluation section ("" when none / dark).
    readme_results: str


@dataclass(frozen=True)
class Verdict:
    """One per-artifact Validation verdict."""

    promote: bool
    result_ref: str  # "Figure 2" / "Table 1" / "none"
    confidence: str  # "high" | "med" | "low"


# --- prompt rendering --------------------------------------------------------


def render_user_message(ctx: ValidationContext) -> str:
    """Return the per-artifact user-turn content (manuscript-free)."""
    lines: list[str] = ["PRODUCERS:"]
    if not ctx.producers:
        lines.append("(none)")
    for basename, snippet in ctx.producers:
        lines.append(f"- {basename}")
        for ln in snippet.strip().splitlines():
            if ln.strip():
                lines.append(f"    {ln}")
    lines.append("README:")
    results = ctx.readme_results.strip()
    lines.append(results if results else "(none)")
    return "\n".join(lines)


def build_messages(ctx: ValidationContext) -> list[dict[str, str]]:
    """Render the few-shot prefix + this artifact's user turn."""
    messages: list[dict[str, str]] = []
    for ex in FEW_SHOTS:
        messages.append({"role": "user", "content": ex["user"]})
        messages.append({"role": "assistant", "content": ex["assistant"]})
    messages.append({"role": "user", "content": render_user_message(ctx)})
    return messages


def build_request_kwargs(ctx: ValidationContext, model: str) -> dict[str, Any]:
    """Return the kwargs accepted by both ``messages.create`` and Batches.

    ``temperature: 0`` and the fixed prompt make verdicts reproducible; the
    system block carries a ``cache_control`` breakpoint (a no-op below the
    model's minimum cacheable length, harmless above it).
    """
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": build_messages(ctx),
        "output_config": {
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
    }


# --- response parsing --------------------------------------------------------


def parse_verdict(text: str) -> Verdict | None:
    """Parse the LLM's JSON output. Returns ``None`` on malformed responses.

    ``promote`` must be a real bool; a bad/missing ``confidence`` normalises to
    ``"low"`` and a missing/empty ``result_ref`` to ``"none"``.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    promote = data.get("promote")
    if not isinstance(promote, bool):
        return None
    confidence = data.get("confidence", "low")
    if confidence not in ("high", "med", "low"):
        confidence = "low"
    result_ref = data.get("result_ref", "none")
    if not isinstance(result_ref, str) or not result_ref.strip():
        result_ref = "none"
    return Verdict(promote=promote, result_ref=result_ref, confidence=confidence)


def extract_text(message: Any) -> str:
    """Return the first text block's content from a Claude response."""
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return block.text or ""
    return ""

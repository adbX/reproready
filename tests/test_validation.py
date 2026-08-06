"""Tests for the phase-2 Validation surface: the prompt/response handlers
(:mod:`reproready.prompts`) and the in-memory, promote-only report flow
(:mod:`reproready.validation`)."""

from __future__ import annotations

from pathlib import Path

from reproready.prompts import (
    OUTPUT_SCHEMA,
    PROMPT_VERSION,
    VALIDATION_MODEL,
    ValidationContext,
    Verdict,
    build_request_kwargs,
    parse_verdict,
)
from reproready.score import score_path
from reproready.validation import (
    KEPT_DETECTOR,
    PROMOTED_DETECTOR,
    apply_verdict,
    build_context,
    is_validation_candidate,
    producer_snippet,
    readme_results_slice,
)

# --- response parsing --------------------------------------------------------


def test_parse_verdict_promote() -> None:
    v = parse_verdict(
        '{"promote": true, "result_ref": "Figure 2", "confidence": "high"}'
    )
    assert v == Verdict(promote=True, result_ref="Figure 2", confidence="high")


def test_parse_verdict_keep() -> None:
    v = parse_verdict('{"promote": false, "result_ref": "none", "confidence": "med"}')
    assert v is not None and v.promote is False and v.confidence == "med"


def test_parse_verdict_rejects_malformed_and_missing_promote() -> None:
    assert parse_verdict("not json") is None
    assert parse_verdict('{"result_ref": "x", "confidence": "high"}') is None
    # promote must be a real bool, not a truthy string.
    assert parse_verdict('{"promote": "yes", "confidence": "high"}') is None


def test_parse_verdict_normalises_confidence_and_result_ref() -> None:
    v = parse_verdict('{"promote": true, "confidence": "wild"}')
    assert v is not None and v.confidence == "low" and v.result_ref == "none"
    v2 = parse_verdict('{"promote": true, "confidence": "high", "result_ref": ""}')
    assert v2 is not None and v2.result_ref == "none"


# --- request shape -----------------------------------------------------------


def test_build_request_kwargs_temperature_zero_and_structured() -> None:
    ctx = ValidationContext(
        producers=[("fig2.py", "savefig('figure2.pdf')")], readme_results=""
    )
    kw = build_request_kwargs(ctx, model=VALIDATION_MODEL)
    assert kw["model"] == VALIDATION_MODEL
    assert kw["temperature"] == 0  # reproducible verdicts
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"] == OUTPUT_SCHEMA
    # 3 few-shots × 2 turns + 1 user turn.
    assert len(kw["messages"]) == 7
    # Cache-key stamps exist.
    assert PROMPT_VERSION == "validate-v1"
    assert VALIDATION_MODEL


# --- pure context helpers ----------------------------------------------------


def test_readme_results_slice_extracts_only_results() -> None:
    md = (
        "## Setup\npip install -r requirements.txt\n"
        "## Results\nFigure 2 is produced by eval.py."
    )
    sliced = readme_results_slice(md)
    assert "Results" in sliced and "Figure 2 is produced by eval.py." in sliced
    assert "pip install" not in sliced
    assert readme_results_slice("") == ""


def test_producer_snippet_strips_and_caps() -> None:
    assert producer_snippet(None) == ""
    assert producer_snippet("1: import x\n2: y = 1") == "import x\ny = 1"
    long = "1: " + "a" * 5000
    assert len(producer_snippet(long)) <= 1000


# --- in-memory promote-only flow ---------------------------------------------


def _candidate_dir(tmp_path: Path) -> Path:
    """A V-candidate artifact: a producer (eval.py) + a results README, and two
    runnable units so Execution is in scope."""
    d = tmp_path / "art"
    d.mkdir()
    (d / "eval.py").write_text(
        "import matplotlib.pyplot as plt\nplt.savefig('figure2.pdf')\n"
    )
    (d / "train.py").write_text("print('train')\n")
    (d / "README.md").write_text(
        "## Setup\npip install -r requirements.txt\n"
        "## Results\nRun eval.py to reproduce Figure 2.\n"
    )
    return d


def test_candidate_detection_and_context(tmp_path: Path) -> None:
    report = score_path(_candidate_dir(tmp_path))
    assert is_validation_candidate(report)
    ctx = build_context(report)
    assert any(name == "eval.py" for name, _ in ctx.producers)
    assert "Figure 2" in ctx.readme_results
    assert "pip install" not in ctx.readme_results  # only the results slice


def test_promote_lifts_v_implementation(tmp_path: Path) -> None:
    from reproready import routing

    report = score_path(_candidate_dir(tmp_path))
    v_cell = routing.Cell("V", routing.IMPLEMENTATION)
    assert report.grid[v_cell].grade == 0.5  # deterministic floor

    promoted = apply_verdict(
        report, Verdict(promote=True, result_ref="Figure 2", confidence="high")
    )
    assert promoted.grid[v_cell].grade == 1.0
    assert promoted.grid[v_cell].detector_id == PROMOTED_DETECTOR
    assert promoted.vector["V"] >= report.vector["V"]


def test_keep_holds_the_floor(tmp_path: Path) -> None:
    from reproready import routing

    report = score_path(_candidate_dir(tmp_path))
    v_cell = routing.Cell("V", routing.IMPLEMENTATION)

    kept = apply_verdict(
        report, Verdict(promote=False, result_ref="none", confidence="low")
    )
    assert kept.grid[v_cell].grade == 0.5
    assert kept.grid[v_cell].detector_id == KEPT_DETECTOR
    # A malformed / missing verdict also holds the floor, never lowers it.
    held = apply_verdict(report, None)
    assert held.grid[v_cell].grade == 0.5

# ReproReady

A static reproduction-readiness scorer for a code artifact: it grades how well
an archive (a directory, `.zip`, or `.tar.gz`) equips an independent researcher
to regenerate the reported results — **without running any code**. It measures
readiness, not executability.

## Layout

```text
src/reproready/        # the package
  inventory.py         # archive/dir walker → RawEntry list (the public reader)
  checker_intake.py     # descriptor-held snapshot + bounded inspection child
  checker.py            # internal snapshot-to-report orchestration
  checker_report.py     # deterministic report construction and encoding
  checker_inventory.py  # bounded direct-file and nested-ZIP inspection
  checker_browser.py    # internal versioned member-browser session
  routing.py           # basename → (stage, channel) routing table
  scope.py             # which stages are in scope for this artifact
  content.py           # targeted member-byte reads
  extract.py           # deterministic per-file evidence summaries
  rubric.py            # deterministic cell grading
  aggregate.py         # cells → stage vector → R, tier, coverage
  score.py             # score_path(): the store-free single-artifact pipeline
  prompts.py           # the optional Validation model call (prompt + parsing)
  validation.py        # promote-only Validation flow over a report
  cli.py               # `reproready score`
  schemas/             # packaged checker report schemas
docs/                  # public checker guide and versioned ruleset
tests/                 # pytest suite (mirrors each module) + test_e2e.py
examples/demo-artifact # a synthetic, well-formed artifact used by the e2e test
```

## Commands

The project uses `uv`. Run everything through it:

```sh
uv sync                     # env + editable install (dev group: pytest, ruff)
uv run pytest               # tests
uv run ruff check           # lint (E4/E7/E9/F + isort)
uv run ruff format --check  # format gate
uv run reproready score examples/demo-artifact   # smoke the CLI
```

CI runs lint and a Python 3.10/3.12/3.13 pytest matrix.

## Architecture notes

- **The score modules are standard-library only.** `inventory`, `routing`,
  `scope`, `content`, `extract`, `rubric`, `aggregate`, `score` import nothing
  outside the stdlib, and that stays true so the released score remains
  reproducible. `rich` is used only by `cli.py`; `anthropic` only by the
  optional Validation call (`validation.py`, lazily imported behind the `llm`
  extra). The rule covers the score modules, not the package. Checker runtime
  dependencies are added only when implementation requires them. `jsonschema`
  is currently a development-only dependency used to test the public report
  schema; the released checker does not expose a schema-validation workflow.
- **`score_path` is the whole pipeline in memory**: inventory → junk filter →
  route → scope → targeted byte reads → evidence → rubric → aggregate, returning
  an `ArtifactReport`. No database, no persistence.
- **Scoring behavior is versioned.** Five stamps identify routing, rubric,
  extraction, the Validation prompt, and promoted tier/scope behavior:
  `ROUTING_VERSION`, `RUBRIC_VERSION`, `EXTRACT_VERSION`, `PROMPT_VERSION`,
  `TIER_SCOPE_VERSION`. Callers decide how those identifiers affect persisted
  reports and cache invalidation.
- **`score_path` defaults to `aggregate.PROMOTED_CONFIG`** (recut tiers so
  tiers 1–3 populate; false-zero scope). Pass an explicit `AggregationConfig`
  to override.
- **The Validation call is promote-only.** It can lift `V/implementation` from
  its `0.5` floor to `1.0`; it never lowers any grade.

## Conventions

- **Test fixtures stay synthetic.** Every archive in the test tree is built
  fresh from a spec dict in a fixture — no real third-party archives are checked
  in. `test_e2e.py` pins the demo artifact's full grid and `R`; a routing /
  rubric / aggregation change that moves the demo's score surfaces there.
- Minimal, clean Python (YAGNI, KISS). Keep dependencies minimal.
- The demo artifact under `examples/` doubles as the golden e2e fixture; changing
  its files changes the pinned e2e expectations.
- The checker report contract lives in
  `src/reproready/schemas/check-report-v1.schema.json`; its rule semantics live
  in `docs/checker-ruleset-v1.md`. Synthetic JSON examples under
  `tests/fixtures/checker-report-v1/` exercise the contract without containing
  third-party artifact data.

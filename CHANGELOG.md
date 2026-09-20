# Changelog

All notable changes to ReproReady are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Unified the saved-report viewer's visual hierarchy across interactive and plain output. One muted teal now identifies navigation and artifacts, while amber marks review questions and reached limits, red marks non-limit inspection failures, and blue marks saved locations. Limits now precedes skipped and failed inspection records. The interactive browser centers and ellipsizes long artifact names without changing report data, keeps complete report and detail fields at supported widths, wraps seven grouped shortcut units across one or two rows, and uses centered, individually outlined page controls with one filled active tab at 92 or more columns. Narrower terminals use a filled title/count indicator. Top-only rules title report pages and separate the saved-report list without enclosing nested evidence boxes; section and help dialogs use square borders. Checker JSON, inspection classifications, and saved reports are unchanged.
- Reworked `reproready view PATH...` into a keyboard-only paged browser. Multiple inputs now open in a searchable full-screen saved-report list; one input opens directly. Every admitted report exposes eight stable pages with named wide-terminal tabs, a compact narrow-terminal position indicator, a section menu, per-report page and detail state, and per-page reading positions. Left and Right change pages, `[` and `]` change reports, Escape returns to the list, and `app.run(mouse=False)` leaves terminal mouse reporting disabled. Rejected files retain fixed error pages, and redirected output and `--plain` still render valid selections sequentially without reopening or changing artifacts.
- Refactored the Rich checker renderer around one reusable report presentation. Interactive pages and linear `check` or `view --plain` output now share section meanings, labeled summaries, evidence grouping, compact selection, source locations, retained-detail disclosures, role-based styling, stable empty pages, and consistent box spacing. Checker JSON, score output, saved-report admission, inspection behavior, and exit precedence are unchanged.
- Revised command help, score tables, diagnostics, and fixed report explanations. The score display now names stages, channel grades, runnable code units, observable coverage, unavailable values, and out-of-scope stages without changing score calculations or score JSON.

## [0.2.0] - 2026-09-13

- Added the public `check_path()` API and `reproready check PATH [--json]` command for the frozen
  eleven-rule `python-v1` static checker. The typed, one-pass API returns schema-v1 reports; the
  bounded terminal view groups observations by topic, elevates inspection limitations, caps examples,
  and leaves the complete retained record in JSON. Existing score behavior remains separate.
- Implemented all eight retained `python-v1` human-review rules over the existing bounded source and
  inventory pass. The eleven-rule catalogue now reports exact three-dot `sys.path` segments,
  download comments with HTTP URLs, reads represented only in nested ZIPs, pandas CSV paths absent
  from a complete container inventory, notebook `!pip install` lines, bounded anonymized-value flows
  to `gdown.download`, stdin calls in canonical entry points, and exact `gs://bucket` authorities in
  resolved `GFile` calls. Rule-specific coverage, dense supporting evidence, global limit ownership,
  deterministic schema-valid JSON, and terminal-safe generic rendering remain internal; the public
  command and `check_path()` remain unavailable.
- Implemented the internal `python.dependencies` rule over each transient parsed Python source,
  supported notebook code cell, direct requirement declaration, and PEP 621
  `project.dependencies` array. The rule emits lexical import, declaration, running-runtime
  standard-library, and obvious local-module evidence; relates exact normalized matches only within
  virtual project and container boundaries; and asks separately about unmatched imports and
  declarations. Unsupported forms, parser and read failures, global limits, dense relationships,
  terminal-safe output, and the `packaging` parser version remain explicit. Retained discovery rules,
  the public command, and `check_path()` remain unavailable.
- Implemented the internal `python.absolute-path` rule over each transient parsed Python file and
  supported notebook code cell. The frozen lexical call positions and five host-independent path
  classes now produce bounded full-call snippets with exact source, member, cell, and line identity;
  partial coverage and global limit ownership remain explicit, deterministic, schema-valid, and
  terminal-safe. Retained discovery rules, the public command, and `check_path()` remain unavailable.
- Added one internal bounded source pass for direct and archived Python plus supported nbformat 4 code
  cells. Dense source identities, physical one-based cells, exact AST lines and syntax, distinct
  notebook outcomes, truthful parser metadata, explicit read blockers, and corrected dependent-rule
  coverage now share the same transient parsed-source context. Retained discovery rules, the public
  command, and `check_path()` remain unavailable.
- Implemented the internal `archive.structure` rule with exact member-linked findings, separate
  coverage records, deterministic observation IDs, bounded full-member verification, valid report
  prefixes at observation and report limits, and a terminal-safe Rich renderer. The public checker
  command and `check_path()` remain unavailable until the remaining rules are implemented.
- Added internal bounded intake for direct Python, nbformat 4 notebooks, supported dependency files,
  ZIP, ZIP64, and nested ZIPs. Stable container and member identities preserve duplicate access;
  fixed expansion, parser, storage, report, memory, and time limits produce explicit partial or error
  reports without extracting directory trees.
- Added the internal versioned member browser with bounded list, literal-search, and line-read
  operations over the completed snapshot. The public checker command and `check_path()` remain
  unavailable until the rules are implemented.
- Added descriptor-verified, no-follow source snapshotting and one killable inspection child with
  parent-monitored resident-memory and elapsed-time ceilings. Focused containment tests cover source
  identity and modification changes, rejected input, snapshot isolation, worker timeout, worker
  memory, and worker crash behavior. This internal slice does not expose the checker command.
- Added the initial checker report schema, `python-v1` ruleset, public guide,
  and synthetic contract reports. The checker command remains under
  development.
- Added the versioned bounded-browser pilot contract and a deterministic synthetic
  input matrix for ZIP, Python, notebook, path, dependency, mutation, and
  terminal-safety behavior, plus the timeout-test checkpoint contract.
- Moved the score specification and implementation reference to the research repository.
- Reduced this repository's documentation to source installation and development setup in the README.
- Removed the documentation site, Zensical dependency, and documentation CI job.

## [0.1.0]

Initial release.

- Static reproduction-readiness scoring for a single code artifact — a
  directory, `.zip`, or `.tar.gz` — computed entirely from the artifact's own
  bytes, without running any code.
- `score_path()` public API returning an `ArtifactReport` (the graded cell
  grid, per-stage vector, score `R`, tier, and coverage).
- `reproready score PATH...` CLI with a `rich`-rendered report and a `--json`
  mode.
- Optional `--validate` promote-only model call (the `llm` extra; needs
  `ANTHROPIC_API_KEY`).

[Unreleased]: https://github.com/adbX/reproready/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/adbX/reproready/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/adbX/reproready/releases/tag/v0.1.0

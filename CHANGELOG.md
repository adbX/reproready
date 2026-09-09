# Changelog

All notable changes to ReproReady are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/adbX/reproready/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/adbX/reproready/releases/tag/v0.1.0

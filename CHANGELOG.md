# Changelog

All notable changes to ReproReady are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

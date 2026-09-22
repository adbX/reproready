# ReproReady

ReproReady is a public Python tool with two separate static-analysis surfaces. The primary checker inspects one regular file and reports exact observations, inspection limits, and questions for a person to review. The separate ReproReady score grades how well an artifact removes manual reproduction work. Neither surface runs artifact code or predicts whether it will execute successfully.

## Development

Use `uv` for the environment and commands:

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
uv run --locked --group docs zensical build --clean --strict
uv build --no-sources
```

Run focused tests during development. `.github/workflows/ci.yml` is the canonical integration procedure: lint and formatting checks, pytest on Python 3.11-3.14, a strict documentation build and inventory check, then a build and clean-install smoke test of both distributions through the public APIs and commands.

## Product and architecture contracts

- The checker accepts exactly one regular file. It does not follow a top-level symbolic link, execute or import artifact code, install dependencies, access the network, or consult a model. Direct supported files and ZIP content are inspected within fixed limits; unsupported regular files still produce a report.
- Findings and review questions describe static evidence. They are not pass/fail decisions or claims about executability, scientific correctness, reproducibility, policy compliance, accuracy, prevalence, or reviewer benefit. Coverage statuses describe what inspection completed.
- Limits, skipped inputs, failed inputs, source mutation, and represented worker failures remain explicit in the report. Never convert partial inspection into apparent success or silently discard a safely retained prefix.
- `src/reproready/schemas/check-report-v1.schema.json` is the machine-readable report contract. Report order and identifiers are deterministic for stable input within one tool and runtime version. Reports exclude host input paths, temporary paths, and raw internal exceptions. Schema or meaning changes require deliberate compatibility and version review.
- `check --json` writes one compact schema-valid object followed by a newline. Findings, review items, unsupported content, partial coverage, and represented worker errors still produce a valid-report success exit. Admission failures and unexpected internal failures retain their documented distinct outcomes.
- `view` validates saved reports without reopening artifacts, fetching schemas, or changing selected files. In a terminal, one valid report opens directly and multiple reports open in a searchable list. Redirected output and `--plain` render sequentially without interaction.
- Every admitted saved report exposes eight stable pages: Artifact, Content analyzed, Limits reached, Checks skipped, Checks failed, Findings, Needs review, and Saved report. The interactive viewer is keyboard-only, preserves per-report page, detail, and reading state, and does not enable terminal mouse reporting.
- The score remains separate from the checker and measures static reproduction readiness, not run probability. Its core pipeline modules (`inventory`, `routing`, `scope`, `content`, `extract`, `rubric`, `aggregate`, and `score`) remain standard-library only. Rich, Textual, checker dependencies, and the optional model dependency stay outside that pipeline.
- Scoring behavior is versioned through its exported routing, rubric, extraction, prompt, and tier/scope identifiers. A behavior change must review those identifiers and the pinned end-to-end expectations.
- Test inputs and archives remain synthetic. Never commit a real third-party artifact as a fixture.

The website Quick start in `docs/index.md`, checker guide in `docs/checker.md`, ruleset in `docs/checker-ruleset-v1.md`, generated checker API page in `docs/api.md`, browser contract in `development/checker-browser-v1.md`, and packaged JSON Schema are the detailed public authorities. Selected checker API documentation comes from public docstrings in `checker.py` and `checker_types.py`; do not duplicate that reference prose in Markdown. Update the relevant authority when public behavior changes instead of duplicating detailed semantics here.

## Task routing

| Change | Read first | Primary implementation and focused tests |
|---|---|---|
| Input admission, snapshotting, or containment | `docs/checker.md` safety and supported-input sections | `checker_intake.py`, `checker.py`; `test_checker_intake.py`, `test_checker_api.py` |
| ZIP inventory, nested archives, or bounded browsing | Checker ruleset and browser contract | `checker_archive.py`, `checker_inventory.py`, `checker_browser.py`; matching checker tests |
| Python source indexing or review rules | Relevant ruleset sections | `checker_python.py`, `checker_python_paths.py`, `checker_python_dependencies.py`, `checker_python_review.py`; matching rule tests |
| Report model, ordering, schema, or public API | Checker guide, ruleset shared semantics, and JSON Schema | `checker_types.py`, `checker_report.py`, `checker.py`, `__init__.py`; API, schema, and package tests |
| Plain terminal output or CLI behavior | Checker guide command and process-outcome sections | `checker_render.py`, `cli.py`; `test_checker_render.py`, `test_checker_cli.py` |
| Interactive saved-report viewer | Checker guide saved-report and interaction sections | `checker_view.py`, `checker_tui.py`; `test_checker_tui.py`, `test_checker_cli.py` |
| Score behavior | Exported version constants, module docstrings, and pinned end-to-end behavior | Score pipeline modules; `test_inventory.py` through `test_aggregate.py`, plus `test_e2e.py` |
| Documentation website or generated checker API | `zensical.toml`, the four files under `docs/` selected by its navigation, and selected checker docstrings | Website sources, styles, JavaScript, and selected public docstrings; strict documentation build and site inventory check |
| Packaging or public exports | `pyproject.toml` and CI build-install job | `pyproject.toml`, `__init__.py`, `cli.py`; `test_checker_package.py` |

Read the relevant authority and the complete affected code section before editing. Follow a data or behavior contract through every caller, renderer, schema, fixture, and document it affects.

## Change-scoped verification

| Change type | Minimum proof |
|---|---|
| Checker intake, inventory, source analysis, or rule | Run the focused test module, then exercise the changed path with `reproready check` on a synthetic file or archive. Inspect the actual terminal or JSON result. |
| Report schema, encoding, or public checker API | Run API and schema tests, generate a report through the public command, and validate or reopen that saved report through the public surface. |
| Rich renderer or CLI | Run renderer and CLI tests, then inspect the actual command at a relevant width. Exercise plain or redirected output, `NO_COLOR`, JSON isolation, and exit behavior when the change can affect them. |
| Textual viewer | Run TUI and CLI tests, then use the actual viewer in a PTY at representative wide and narrow sizes. Exercise the changed navigation, retained state, and exit path. |
| Score pipeline | Run the affected score tests and `uv run reproready score examples/demo-artifact --json`. Review version stamps when output or semantics change. |
| Documentation website or generated checker API | Run the exact locked strict build and site inventory check, then inspect the rendered surface at representative desktop and mobile widths. Exercise keyboard navigation, theme persistence, local assets, search, links, and the documented Quick start. |
| Packaging, dependencies, or public exports | Build the distributions, install the wheel in a clean environment, and exercise the affected import and command without relying on the source checkout. |

A test is not a substitute for exercising the changed CLI or TUI surface. Keep permanent tests only for observable contracts and plausible regressions.

## Release procedure

Releases use reviewed generated GitHub release notes. There is no project changelog.

1. Update the version in `pyproject.toml`, `src/reproready/__init__.py`, the report fallback, package tests, citation metadata, and the worked report. Add the release date to `CITATION.cff` only for the final candidate.
2. From a clean candidate commit, run the full CI procedure, `uv build --no-sources`, and `uvx twine check --strict dist/*`. Inspect both archives, install each outside the checkout, and exercise `check`, `view`, the Python API and schema, and the score command.
3. Push the exact candidate and require a successful CI run for that commit. Create the matching `vX.Y.Z` tag and a draft GitHub release. Generate its notes, remove private or development-only details and unsupported claims, and attach the verified wheel and source distribution without rebuilding them. After final review, publish it as a prerelease so the read-only release validator can fetch it.
4. Confirm that the `pypi` GitHub environment requires the intended owner's approval and that PyPI Trusted Publishing names this repository, `.github/workflows/release.yml`, and that environment. Manually dispatch the Release workflow from the default branch with `release_tag`, both SHA-256 values, and the review confirmation. The workflow checks out and validates the protected tag rather than publishing the default branch.
5. After PyPI succeeds, promote the prerelease to the final GitHub release. Verify anonymous uv and isolated-pip installation, rendered metadata, both release assets, and matching GitHub and PyPI hashes before recording the release as complete.

If an upload is partial, stop and compare every published filename and SHA-256 value with the frozen candidate. Resume only with byte-identical missing files. PyPI filenames cannot be replaced. Yank an incorrect release and publish a new version rather than trying to overwrite it.

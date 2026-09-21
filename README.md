# ReproReady

[![CI](https://github.com/adbX/reproready/actions/workflows/ci.yml/badge.svg)](https://github.com/adbX/reproready/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/adbX/reproready/blob/v0.2.0/LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.14-blue.svg)](https://github.com/adbX/reproready/blob/v0.2.0/pyproject.toml)

ReproReady is a static checker for research artifacts that inspects code, data, and other files without running it. It reports observations about file paths, dependencies, and data access, with source locations and questions for review. ReproReady aims to assist a human replicator and does not reproduce results or try to predict whether code will run.

## Install

Requires Python 3.11 or newer on macOS or Linux. Install the exact release with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install 'reproready==0.2.0'
```

Alternatively, install it with pip in an isolated virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'reproready==0.2.0'
```

For the Python API in a uv project, add the package as a project dependency:

```sh
uv add 'reproready==0.2.0'
```

## Check your code

Pass one ZIP archive or a [supported Python, notebook, or dependency file](https://github.com/adbX/reproready/blob/v0.2.0/docs/checker.md#supported-inputs). Directories are not accepted.

```sh
reproready check artifact.zip
reproready check artifact.zip --json > report.json
reproready view report.json another-report.json
reproready view saved-run/
reproready view saved-run/ --plain --all
```

The terminal report separates the same eight stable pages: Artifact, Content analyzed, Limits reached, Checks skipped, Checks failed, Findings, Needs review, and Saved report. `check --json` writes one compact saved report. In a terminal, `view` opens one report directly or starts multiple reports in a searchable full-screen list without reopening their artifacts. Left and Right change pages, `[` and `]` change reports, `s` opens the page menu, Escape returns to the list, `a` toggles all saved detail, `?` shows every key, and `q` quits. The viewer is keyboard-only and does not enable terminal mouse reporting. Its shortcut footer keeps paired page and report keys together and wraps whole hints on narrow terminals. Up and Down, PgUp and PgDn, and Home and End scroll only the current page. A directory selects top-level JSON files plus `report.json` in immediate child directories. Redirected output and `--plain` print the selected reports sequentially.

## Documentation

- [Checker guide](https://github.com/adbX/reproready/blob/v0.2.0/docs/checker.md): supported inputs, report interpretation, and inspection limits.
- [Python API](https://github.com/adbX/reproready/blob/v0.2.0/docs/checker.md#python-api): use the checker from another Python project.
- [Ruleset](https://github.com/adbX/reproready/blob/v0.2.0/docs/checker-ruleset-v1.md): what each check looks for and what it cannot determine.
- [Security policy](https://github.com/adbX/reproready/blob/v0.2.0/SECURITY.md): privately report vulnerabilities without posting sensitive artifacts publicly.
- [Worked example](https://github.com/adbX/reproready/blob/v0.2.0/docs/checker.md#worked-example): sample code and its report.

The package also includes a separate score command; see `reproready score --help`.

## License

ReproReady is available under the [MIT License](https://github.com/adbX/reproready/blob/v0.2.0/LICENSE).

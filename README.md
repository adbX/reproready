# ReproReady

[![CI](https://github.com/adbX/reproready/actions/workflows/ci.yml/badge.svg)](https://github.com/adbX/reproready/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

ReproReady is a static checker for research artifacts that inspects code, data, and other files without running it. It reports observations about file paths, dependencies, and data access, with source locations and questions for review. ReproReady aims to assist a human replicator and does not reproduce results or try to predict whether code will run.

## Install

Requires Python 3.10 or newer on macOS or Linux. Install with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install 'reproready @ git+https://github.com/adbX/reproready.git@v0.2.0'
```

## Check your code

Pass one ZIP archive or a [supported Python, notebook, or dependency file](docs/checker.md#supported-inputs). Directories are not accepted.

```sh
reproready check artifact.zip
reproready check artifact.zip --json
```

The terminal report groups findings and questions for review and flags incomplete inspection. Add `--json` for machine-readable output.

## Documentation

- [Checker guide](docs/checker.md): supported inputs, report interpretation, and inspection limits.
- [Python API](docs/checker.md#python-api): use the checker from another Python project.
- [Ruleset](docs/checker-ruleset-v1.md): what each check looks for and what it cannot determine.
- [Worked example](docs/checker.md#worked-example): sample code and its report.

The package also includes a separate score command; see `reproready score --help`.

## License

ReproReady is available under the [MIT License](LICENSE).

# ReproReady

[![CI](https://github.com/adbX/reproready/actions/workflows/ci.yml/badge.svg)](https://github.com/adbX/reproready/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

ReproReady provides two separate static tools for code artifacts. The checker reports exact observations, inspection limits, and questions for a person to interpret. The score measures static reproduction readiness as `R`. Neither tool runs artifact code or predicts whether it will execute successfully.

## Requirements

- Python 3.10 or newer.
- [uv](https://docs.astral.sh/uv/) for the commands below.
- macOS or Linux for the checker. The score retains its existing platform support.

## Install the command

Install the immutable `v0.2.0` release as a standalone command:

```sh
uv tool install 'reproready @ git+https://github.com/adbX/reproready.git@v0.2.0'
reproready --help
```

To include the score's optional model dependency:

```sh
uv tool install 'reproready[llm] @ git+https://github.com/adbX/reproready.git@v0.2.0'
export ANTHROPIC_API_KEY=...
```

## Check one artifact

The Python-only checker accepts one regular file. It supports direct Python, nbformat 4 Python notebooks, direct requirements files, PEP 621 dependencies in `pyproject.toml`, ZIP, ZIP64, and nested ZIP inspection. Other regular files receive a valid report that names the unsupported form; directories and top-level symbolic links are rejected.

```sh
reproready check artifact.zip
reproready check artifact.zip --json
```

The default report groups findings and human-review observations by topic, shows at most three examples per group, and elevates inspection limitations. It summarizes all eleven rule statuses without printing every routine rule or standalone evidence record. `--json` returns the complete schema-v1 technical record retained within the reported limits. Redirected output is plain text, and `NO_COLOR` disables styling explicitly.

Use the typed Python API for the same one-pass inspection:

```python
from reproready import CheckInputError, CheckReport, check_path

try:
    report: CheckReport = check_path("artifact.zip")
except CheckInputError as error:
    print(error.code, error.message)
else:
    document = report.to_dict()
```

`CheckReport.to_dict()` returns the report's retained schema dictionary without rerunning inspection or copying the complete document. Treat the returned dictionary as read-only.

See the [checker guide](docs/checker.md), [`python-v1` ruleset](docs/checker-ruleset-v1.md), [JSON Schema](src/reproready/schemas/check-report-v1.schema.json), and [worked input](examples/checker-demo.py).

## Score one or more artifacts

The existing score interface remains separate:

```sh
reproready score artifact.zip
reproready score artifact.zip --json
```

```python
from reproready import ArtifactReport, score_path

report: ArtifactReport = score_path("artifact.zip")
print(report.r, report.tier)
```

The score accepts directories, ZIP archives, and tar-gzip archives. Its optional `--validate` mode requires the `llm` extra and an `ANTHROPIC_API_KEY`.

## Install the library

Add the tagged release to another uv project:

```sh
uv add 'reproready @ git+https://github.com/adbX/reproready.git@v0.2.0'
```

## Development checkout

```sh
git clone https://github.com/adbX/reproready.git
cd reproready
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run reproready check examples/checker-demo.py
uv run reproready score examples/demo-artifact --json
```

Use `uv sync --extra llm` when developing the optional score validation integration.

## Boundaries

Checker observations are static facts or questions for interpretation. They are not grades, policy decisions, accuracy claims, or evidence that reproduction succeeds or fails. The checker does not execute code, install dependencies, access the network, or expose the ReproReady score. The score remains a measure of static readiness and author-removable work, not executability.

## License

ReproReady is available under the [MIT License](LICENSE).

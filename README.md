# ReproReady

[![CI](https://github.com/adbX/reproready/actions/workflows/ci.yml/badge.svg)](https://github.com/adbX/reproready/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

ReproReady is a static reproduction-readiness scorer for code artifacts.

## Requirements

- Python 3.10 or newer.
- [uv](https://docs.astral.sh/uv/) for the commands below.

## Install the command

Install the current source as a standalone command:

```sh
uv tool install 'reproready @ git+https://github.com/adbX/reproready.git'
reproready --help
```

To include the optional model dependency:

```sh
uv tool install 'reproready[llm] @ git+https://github.com/adbX/reproready.git'
export ANTHROPIC_API_KEY=...
```

## Install the library

Add the current source to another uv project:

```sh
uv add 'reproready @ git+https://github.com/adbX/reproready.git'
```

Include the optional model dependency when needed:

```sh
uv add 'reproready[llm] @ git+https://github.com/adbX/reproready.git'
export ANTHROPIC_API_KEY=...
```

## Development checkout

```sh
git clone https://github.com/adbX/reproready.git
cd reproready
uv sync
uv run ruff check
uv run ruff format --check
uv run pytest
uv run reproready score examples/demo-artifact --json
```

Use `uv sync --extra llm` when developing the optional model integration.

## Checker under development

The separate read-only checker is being implemented against its frozen
[`reproready check` guide](docs/checker.md),
[`python-v1` ruleset](docs/checker-ruleset-v1.md), and
[versioned JSON Schema](src/reproready/schemas/check-report-v1.schema.json).
Bounded intake, member browsing, the `archive.structure` rule, and exact one-based Python and notebook
source indexing now exist as internal interfaces. The absolute-path and dependency rules, the command,
and the `check_path()` API are not available yet; the existing score command and API remain unchanged.

## License

ReproReady is available under the [MIT License](LICENSE).

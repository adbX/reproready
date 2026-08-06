# Contributing to ReproReady

Thanks for your interest. ReproReady is a small, dependency-light tool, and the
goal is to keep it that way.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/) for the environment and
packages.

```sh
git clone https://github.com/adbX/reproready
cd reproready
uv sync            # installs the package plus the dev group
uv run pytest      # run the test suite
```

## Before opening a pull request

Run the same checks CI runs:

```sh
uv run ruff check
uv run ruff format --check
uv run pytest
uv run zensical build
```

## Design constraints

- **The scoring core stays standard-library only.** `rich` is for the CLI and
  `anthropic` for the optional Validation call — nothing else should reach into
  the core (`inventory`, `routing`, `scope`, `content`, `extract`, `rubric`,
  `aggregate`).
- **Scoring is deterministic and versioned.** Any change to the deterministic
  logic must bump the matching version stamp (`ROUTING_VERSION`,
  `RUBRIC_VERSION`, `EXTRACT_VERSION`, `PROMPT_VERSION`, `TIER_SCOPE_VERSION`)
  so that scores produced by different logic are never conflated.
- **Test fixtures are synthetic.** Do not add real third-party archives to the
  test tree; build small synthetic artifacts in fixtures instead.

## What ReproReady measures

ReproReady scores *readiness*, not *executability* — how well an artifact
equips an independent researcher to reproduce the reported results, judged
without running any code. Proposals that would turn it into an execution
harness are out of scope; see [`docs/spec.md`](docs/spec.md) for the boundary.

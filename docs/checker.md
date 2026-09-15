# ReproReady checker

The checker inspects one regular file without running artifact code. Authors and reviewers can use its findings, source locations, and questions for review to investigate paths, dependencies, and data access. It is separate from the ReproReady score.

Version `0.2.0` uses report schema version `1` and the eleven-rule [`python-v1` ruleset](checker-ruleset-v1.md).

## Installation

Requires Python 3.10 or newer on macOS or Linux. Install the `v0.2.0` Git tag with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install 'reproready @ git+https://github.com/adbX/reproready.git@v0.2.0'
```

To use the Python API in another uv project:

```sh
uv add 'reproready @ git+https://github.com/adbX/reproready.git@v0.2.0'
```

## Command-line interface

Pass exactly one file path. Use `--json` for the full machine-readable report:

```sh
reproready check artifact.zip
reproready check artifact.zip --json
```

Terminal output is noninteractive and adapts to terminal width. Redirected output is plain text; `NO_COLOR` disables styling. Meaning never depends on color. Version `0.2.0` has no `--details` mode or saved human-readable report.

## Interpreting output

Read inspection limits before interpreting findings. The report starts with the sanitized filename, detected kind, member and source counts, then any inventory issues, reached limits, incomplete sources, or exceptional rule coverage.

Findings describe observed conditions; questions for review need context before a decision. [Rule statuses](checker-ruleset-v1.md#shared-coverage-semantics) describe inspection coverage, not whether an artifact passes. No findings does not prove that a condition is absent or that code is correct or reproducible.

The terminal separates findings from questions for review and groups each by rule, observation kind, and condition code. A *topic* is one such group; an *occurrence* is one observation at a reported location. Multiple occurrences do not establish multiple defects or a shared cause.

Each group shows at most three examples in deterministic report order, with sanitized member names, duplicate ordinals where needed, notebook cells, and source lines. Exact omitted counts describe examples hidden from the terminal, not content left uninspected. Only evidence directly linked to displayed examples appears there.

The summary counts findings, review occurrences, distinct review topics, and every represented rule status. Routine `complete` and `not_applicable` rules have no individual terminal rows; JSON retains every rule.

### JSON output

`--json` writes exactly one schema-valid object followed by a newline, with no diagnostic on standard error for a valid report. It includes all retained artifact, runtime, limit, inventory, source, evidence, observation, coverage, and relationship records. Ordinary imports, standard-library classifications, and dependency declarations remain available here even when omitted from the terminal.

The [JSON Schema](../src/reproready/schemas/check-report-v1.schema.json) defines the full report. The checker does not produce HTML, JSON Lines, batch manifests, or a second artifact inventory.

## Python API

`check_path()` performs the same one-pass inspection and returns `CheckReport`:

```python
from reproready import CheckInputError, CheckMember, CheckReport, check_path

try:
    report: CheckReport = check_path("artifact.zip")
except CheckInputError as error:
    print(error.code, error.message)
else:
    members: tuple[CheckMember, ...] = report.members
    document = report.to_dict()
```

`CheckReport` exposes version fields (`schema_version`, `tool_version`, `ruleset_version`), artifact/runtime/limit records, `inventory_status`, members, inventory issues, the source index, and ordered rule results. `CheckMember` exposes container and member IDs, parent member, decoded name, duplicate ordinal, kind, compressed and expanded sizes, read and integrity states, and member issues.

`to_dict()` returns the retained report dictionary without rereading, reinspecting, or copying it. Treat the dictionary as read-only.

Top-level input rejections raise `CheckInputError` with fixed `code` and `message` values, never a host path or raw operating-system exception. Codes are `source_not_found`, `source_not_accessible`, `top_level_link`, `not_regular_file`, and `unsupported_system`. Unexpected internal exceptions propagate.

Source mutation, an input-size ceiling, a represented worker timeout or memory limit, and a worker crash return a valid report when the parent can produce one within its limits. These are inspection outcomes, not top-level input rejections.

## Supported inputs

| Input or content | Version 0.2.0 behavior |
|---|---|
| Direct `.py` | Python source, including direct `setup.py`. |
| Direct nbformat 4 `.ipynb` | Supported when code-cell source and language metadata meet the [ruleset requirements](checker-ruleset-v1.md#pythonabsolute-path). |
| Direct `requirements*.txt` | Dependency declarations. |
| Direct `pyproject.toml` | PEP 621 `project.dependencies` array. |
| ZIP and ZIP64, including nested ZIPs | Supported within fixed inspection limits. |
| R source and metadata | Visible but unsupported; no R parser or rule runs. |
| Tar, gzip, PDF, DOCX, executables, other regular files | Valid report identifying the unsupported format. |
| Shell source, archived `setup.py`, Conda, Poetry, PDM, Pipenv, lock files, constraints, Dockerfiles, requirement includes | Visible but not semantically analyzed. |
| Directories, top-level links, devices, sockets, other non-regular paths | Rejected before content is read. |

Operating systems other than macOS and Linux are rejected before the input path is accessed. [Input classification](checker-ruleset-v1.md#input-classification) uses the case-insensitive basename before ZIP detection, so a DOCX container or ZIP payload named `.py` is not recursively inspected.

## Reports and fixed limits

Reports include the sanitized artifact name, kind, byte size, snapshot state, SHA-256 when available, runtime and parser versions, effective and reached limits, inventory, source index, and every rule result. They exclude the host's absolute input path and checker temporary paths.

[Fixed limits](checker-ruleset-v1.md#fixed-limits) cover input and expanded sizes, ZIP depth and member count, source sizes, memory, temporary storage, elapsed time, observations, and report size. The reference defines counting, retention order, and reserved capacity for reporting reached limits. Limits are not user-adjustable in v1. Snippet truncation is a display bound, not incomplete rule coverage.

Reports are deterministic for stable input within one tool and runtime version, not necessarily across Python versions or timeout boundaries.

## Process outcomes

| Exit | Meaning |
|---:|---|
| 0 | A valid report exists, including findings, questions for review, partial coverage, unsupported content, or a represented worker error. |
| 1 | An unexpected internal failure prevents a valid report. Standard error receives one fixed diagnostic without raw exception text or artifact data. |
| 2 | `argparse` rejects the invocation, the operating system is unsupported, or top-level input admission rejects the path. |

Findings and questions for review do not change the exit status.

## Worked example

[`examples/checker-demo.py`](../examples/checker-demo.py) produces one lexical path finding and two review topics:

```sh
reproready check examples/checker-demo.py
reproready check examples/checker-demo.py --json
```

The groups are `posix_absolute_path`, `three_dot_path_segment`, and `download_comment_with_http_url`. The [example JSON report](../examples/checker-demo-report.json) also includes ordinary `pathlib` and `sys` evidence omitted from the terminal.

## Safety boundary and nonclaims

The checker snapshots one descriptor-verified regular file without following a top-level link. A separate process inspects only the completed snapshot while the parent enforces time and memory limits. Archive members are not extracted into a directory tree.

The checker does not execute or import artifact code, install dependencies, access the network, or consult a model. It does not establish executability, scientific correctness, reproducibility, policy compliance, accuracy, prevalence, or reviewer benefit. Decisions beyond the reported static evidence remain with the reader.

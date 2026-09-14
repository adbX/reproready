# ReproReady checker

The ReproReady checker is a read-only static inspection tool for one regular file. It reports exact observations, inspection limits, and questions that require a person's judgment without running artifact code. It is separate from the ReproReady score and does not emit `R`, stages, tiers, or grades.

Version `0.2.0` releases schema version `1` and the eleven-rule [`python-v1` ruleset](checker-ruleset-v1.md). The [JSON Schema](../src/reproready/schemas/check-report-v1.schema.json) defines the complete technical report.

## Installation

Install the immutable Git tag with uv:

```sh
uv tool install 'reproready @ git+https://github.com/adbX/reproready.git@v0.2.0'
```

Add the library to another uv project:

```sh
uv add 'reproready @ git+https://github.com/adbX/reproready.git@v0.2.0'
```

## Command-line interface

The command accepts exactly one path:

```sh
reproready check artifact.zip
reproready check artifact.zip --json
```

The default output is a bounded, noninteractive terminal report. Its reading order is:

1. The sanitized artifact basename, detected kind, inventory-member count, and source-record count.
2. Inspection limitations, when any inventory issue, reached limit, incomplete source, or exceptional rule coverage exists.
3. Exact finding and human-review occurrence counts, the number of distinct human-review topics, and aggregate counts for every represented rule status.
4. A legend stating that rule status describes coverage rather than an artifact pass, and that human-review observations require interpretation rather than establish defects.
5. Findings and human-review observations in separate sections, grouped by rule, observation kind, and condition code.

Routine `complete` and `not_applicable` rules do not receive individual rows. Their counts remain in the aggregate coverage summary, and every rule remains present in JSON.

A *topic* is one presentation group with the same rule, observation kind, and condition code. An *occurrence* is one emitted observation at one reported location. Several occurrences in one topic do not establish several defects or a shared cause.

Each group shows at most three examples in deterministic report order. Member and source identifiers resolve to the sanitized member name, duplicate ordinal when needed, notebook cell, and source line. The report states the exact number omitted from the terminal view. This display omission is separate from an inspection limit, which means content was not inspected or retained within a fixed engine boundary.

Only directly linked evidence needed to understand a displayed example appears in the terminal. Ordinary imports, standard-library classifications, dependency declarations, complete per-rule records, and other standalone evidence remain in JSON. Version `0.2.0` has no `--details` mode or saved human-readable report.

Rich styling and terminal width detection are automatic. Redirected output is plain text, and setting `NO_COLOR` disables styling. Color is never the only indication of a finding, review topic, inspection limitation, or technical error.

### JSON output

`--json` writes exactly one schema-valid JSON object followed by a newline. It includes every retained artifact, runtime, limit, inventory, source, evidence, observation, coverage, and relationship record. A valid report writes no diagnostic to standard error.

JSON is the exhaustive inspection interface. The checker does not create an HTML report, JSON Lines stream, batch manifest, or second artifact inventory.

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

`CheckReport` exposes:

- `schema_version`, `tool_version`, and `ruleset_version`.
- The artifact, runtime, and limit records.
- `inventory_status`, a `CheckMember` sequence, and inventory issues.
- The source index and ordered rule results.

`CheckMember` exposes the container and member identifiers, parent member, decoded name, duplicate ordinal, kind, compressed and expanded sizes, read and integrity states, and member issues.

`to_dict()` returns the exact schema-shaped dictionary retained from the completed producer document. It does not reread the artifact, rerun inspection, or deep-copy the report. Treat the returned dictionary as borrowed and read-only.

Top-level admission rejections raise `CheckInputError`, whose `code` and `message` are fixed and never include the host path or raw operating-system exception. Released admission codes are `source_not_found`, `source_not_accessible`, `top_level_link`, `not_regular_file`, and `unsupported_system`. Unexpected internal exceptions propagate from the Python API.

A source mutation, input-size ceiling, represented worker timeout, represented memory limit, or worker crash returns a valid `CheckReport` when the bounded parent can produce one. These states are reportable inspection outcomes rather than top-level admission errors.

## Supported inputs

| Input or content | Version 0.2.0 behavior |
|---|---|
| Direct `.py` | Supported Python source. |
| Direct nbformat 4 `.ipynb` | Supported when code-cell source and language metadata meet the ruleset contract. |
| Direct `requirements*.txt` | Supported dependency declarations. |
| Direct `pyproject.toml` | Supports the PEP 621 `project.dependencies` array. |
| ZIP and ZIP64 | Supported with fixed expansion, member, time, memory, and storage limits. |
| Nested ZIP | Supported through depth 3 beyond the outer ZIP. |
| R source and R metadata | Visible but unsupported. No R parser or R rule runs. |
| Tar, gzip, PDF, DOCX, executables, and other regular files | Produce a valid report naming the unsupported format. |
| Shell source, archived `setup.py`, Conda, Poetry, PDM, Pipenv, lock files, constraints, Dockerfiles, and requirement includes | Visible but not semantically analyzed. A direct `setup.py` follows direct `.py` classification. |
| Directories, top-level links, devices, sockets, and other non-regular paths | Rejected before content is read. |

The checker supports macOS and Linux. Other operating systems are rejected before the input path is accessed. The score command retains its existing platform and input support.

Direct classification uses the case-insensitive basename before ZIP detection. Supported source suffixes take precedence, followed by unsupported archive and document suffixes, ZIP detection, and then unsupported regular input. This prevents a DOCX container or ZIP payload named `.py` from receiving unintended recursive inspection.

## Reports and fixed limits

Every report includes the sanitized artifact display name, detected kind, byte size, snapshot state and SHA-256 when available, runtime and parser versions, effective and reached limits, member inventory, source index, and one result for every released rule. It never includes the source machine's absolute input path or a checker temporary path.

Rule results use `complete`, `partial`, `unsupported`, `error`, or `not_applicable`. Findings are independent of coverage. No status is an artifact verdict.

| Limit | Value |
|---|---:|
| Input bytes | 2 GiB |
| ZIP members across all containers | 100,000 |
| Nested ZIP depth beyond the outer ZIP | 3 |
| Expanded bytes for one member | 512 MiB |
| Expanded bytes across the artifact | 4 GiB |
| One Python or dependency source | 8 MiB |
| One notebook document | 32 MiB |
| Worker resident memory | 1 GiB |
| Checker-owned temporary storage | 4 GiB |
| Inspection elapsed time | 120 seconds |
| Observations | 10,000 |
| Encoded JSON report | 64 MiB |
| Observation snippet | 240 Unicode code points |

The report reserves capacity for reached-limit state and valid closing structure. Snippet truncation is a display bound and does not make rule coverage partial. The [ruleset](checker-ruleset-v1.md#fixed-limits) defines exact counting, ordering, and truncation behavior.

Reports are deterministic within one tool and runtime version for the same stable input. The contract does not promise identical runtime metadata or JSON bytes across Python versions and timeout boundaries.

## Process outcomes

| Exit | Meaning |
|---:|---|
| 0 | A valid report exists, including findings, human-review observations, partial coverage, unsupported content, or a represented worker error. |
| 1 | An unexpected internal failure prevents a valid report. Standard error receives one fixed diagnostic without raw exception text or artifact data. |
| 2 | `argparse` rejects the invocation, the operating system is unsupported, or top-level admission rejects the path. |

Findings and human-review observations do not change the exit status.

## Worked example

The clean-room [`examples/checker-demo.py`](../examples/checker-demo.py) produces one lexical path finding and two human-review topics:

```sh
reproready check examples/checker-demo.py
reproready check examples/checker-demo.py --json
```

The terminal report shows the `posix_absolute_path`, `three_dot_path_segment`, and `download_comment_with_http_url` groups in researcher-facing language. The exact generated technical document is [`examples/checker-demo-report.json`](../examples/checker-demo-report.json). It includes the ordinary `pathlib` and `sys` evidence omitted from terminal output.

## Safety boundary and nonclaims

The checker snapshots one descriptor-verified regular file without following a top-level link. One killable child inspects only that completed snapshot while the parent monitors fixed time and resident-memory ceilings. Archive members are not extracted into a directory tree. The checker does not execute or import artifact code, install dependencies, access the network, or consult a model.

The checker does not establish executability, scientific correctness, reproducibility, policy compliance, accuracy, prevalence, or reviewer benefit. It does not treat absent observations as proof that a condition is absent. A person remains responsible for decisions that depend on context beyond the exact static evidence.

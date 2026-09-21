# ReproReady checker

The checker inspects one regular file without running artifact code. Authors and reviewers can use its findings, source locations, and questions for review to investigate paths, dependencies, and data access. It is separate from the ReproReady score.

Version `0.2.0` uses report schema version `1` and the eleven-rule [`python-v1` ruleset](checker-ruleset-v1.md).

## Installation

Requires Python 3.11 or newer on macOS or Linux. Install the current release with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install reproready
```

To try the command without keeping it installed:

```sh
uvx reproready check artifact.zip
```

Alternatively, install it with pip in an isolated virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install reproready
```

To use the Python API in another uv project:

```sh
uv add reproready
```

## Command-line interface

Pass exactly one file path to `check`. Use `--json` to save the complete machine-readable report. Use `view` to validate and browse one or more saved reports without reopening their original artifacts:

```sh
reproready check artifact.zip
reproready check artifact.zip --json > report.json
reproready view report.json another-report.json
reproready view saved-run/
reproready view saved-run/ --plain --all
```

`check` output is noninteractive and adapts to terminal width. In a terminal, one supplied report opens on its Artifact page; multiple reports open in a searchable full-screen list. Every admitted report has eight stable pages: Artifact, Content analyzed, Limits reached, Checks skipped, Checks failed, Findings, Needs review, and Saved report. Wide terminals show named tabs. Narrow terminals show the current page, its position among eight pages, and the `s` page-menu key.

The viewer is keyboard-only and does not enable terminal mouse reporting. Left and Right change pages, `[` and `]` change reports, `s` opens the page menu, and Escape returns to the report list. Up and Down scroll the current page; PgUp and PgDn scroll by a viewport; Home and End move within that page. In lists, Up and Down move the selection, Enter opens it, `/` focuses saved-report search, and Escape leaves search before returning. `a` toggles all saved detail for the current report, `?` shows every key, and `q` quits. The footer groups page and report pairs and wraps complete shortcut hints instead of hiding them at narrow widths. Redirected output and `view --plain` print reports sequentially without waiting for input. `NO_COLOR` disables styling, and labels, position, and selection remain sufficient without color. `check` does not save implicitly, and the viewer accepts checker schema-v1 reports rather than score JSON.

## Interpreting output

Read Limits reached, Checks skipped, and Checks failed before interpreting findings. Each page states its purpose, shows a summary grounded in saved report records, and then shows its evidence groups. Counts use labeled values or tables. The Content analyzed page keeps archive entries, source files, notebook documents, notebook cells, and dependency records as separate units. Teal identifies navigation and the current artifact; amber marks review questions and reached limits; red marks recorded non-limit inspection failures; and blue marks saved source locations.

`Findings` reports observed archive or path conditions. `Needs review` can contain nine subject boxes: Dependencies, Python search paths, Download comments, Files inside archives, CSV files, Notebook setup, Download identifiers, User input, and Cloud storage. Each nonempty box asks a question and shows saved source locations. Dependencies retains unmatched imports and unmatched declarations as different conditions.

The compact view shows at most three distinct names or items per condition in deterministic report order. It preserves nested containers, duplicate-entry ordinals, notebook cells, source lines, and useful linked evidence. An omission notice distinguishes detail hidden by the display from shortened snippets and content that was not inspected. `view --all` starts every report with all saved detail visible; `a` changes the current report during an interactive session. Vertical scrolling stays with the selected page, and each report remembers its page, detail mode, and reading position while the viewer remains open.

All eight pages remain available when a page has no saved records. An empty Findings or Needs review page reports only that no corresponding items are saved; it does not prove that code is correct, reproducible, free of an uninspected condition, or already reviewed. [Rule statuses](checker-ruleset-v1.md#shared-coverage-semantics) describe inspection coverage, not whether an artifact passes.

### JSON output and saved reports

`check --json` writes exactly one compact schema-valid object followed by a newline, with no diagnostic on standard error for a valid report. It includes all retained artifact, runtime, limit, inventory, source, evidence, observation, coverage, and relationship records. Ordinary imports, standard-library classifications, and dependency declarations remain available here even when omitted from the compact terminal display.

`view` accepts explicit report paths and directories. A directory selects case-insensitive top-level `.json` files plus an exact `report.json` in each immediate real child directory, in deterministic path order; selection does not recurse or follow directory links. Each report must be one regular, non-symbolic-link file of at most 128 MiB. The command admits compact output and older pretty-printed schema-v1 reports under that limit, uses strict UTF-8 and JSON decoding, rejects non-finite numbers, validates the packaged schema and cross-record references, and never fetches a schema or artifact. Invalid selected files remain unchanged and do not prevent navigation to valid reports. A valid saved report remains viewable after the original artifact changes or disappears.

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

| Command | Exit | Meaning |
|---|---:|---|
| `check` | 0 | A valid report exists, including findings, review items, partial coverage, unsupported content, or a represented worker error. |
| `check` | 1 | An unexpected internal failure prevents a valid report. Standard error receives one fixed diagnostic without raw exception text or artifact data. |
| `check` | 2 | `argparse` rejects the invocation, the operating system is unsupported, or top-level input admission rejects the path. |
| `view` | 0 | The saved checker report was validated and displayed. Its represented findings or failures do not change the exit status. |
| `view` | 1 | An unexpected internal failure prevents display. |
| `view` | 2 | The saved input is missing, unsafe, malformed, incompatible, schema-invalid, or has broken references. No partial report is written to standard output. |

Findings and review items do not change either command's exit status.

## Worked example

[`examples/checker-demo.py`](../examples/checker-demo.py) produces one lexical path finding and two review boxes:

```sh
reproready check examples/checker-demo.py
reproready check examples/checker-demo.py --json > checker-demo-report.json
reproready view checker-demo-report.json --all
```

The display shows `POSIX absolute paths` under Findings, plus `Python search paths` and `Download comments` under Needs review. The [example JSON report](../examples/checker-demo-report.json) also includes ordinary `pathlib` and `sys` evidence omitted from the compact terminal view.

## Safety boundary and nonclaims

The checker snapshots one descriptor-verified regular file without following a top-level link. A separate process inspects only the completed snapshot while the parent enforces time and memory limits. Archive members are not extracted into a directory tree.

The checker does not execute or import artifact code, install dependencies, access the network, or consult a model. It does not establish executability, scientific correctness, reproducibility, policy compliance, accuracy, prevalence, or reviewer benefit. Decisions beyond the reported static evidence remain with the reader.

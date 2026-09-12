# ReproReady checker

The ReproReady checker is a read-only static inspection command under development. It will read one regular file without running artifact code and report exact observations, inspection limits, and questions that require a person's judgment. It is separate from the ReproReady score and does not emit `R`, stages, tiers, or grades.

The report contract and initial rule semantics are frozen while implementation proceeds:

- [JSON report schema v1](../src/reproready/schemas/check-report-v1.schema.json)
- [Python ruleset v1](checker-ruleset-v1.md)
- [Bounded browser pilot contract](checker-browser-v1.md)
- [Synthetic report examples](../tests/fixtures/checker-report-v1/)

`reproready check` and `check_path()` are not available yet. The current package continues to provide `reproready score` and `score_path()` unchanged.

The bounded browser, `archive.structure` result, and exact Python source index are implemented as
internal development interfaces, not as a public command or model-assisted checker mode. The browser
exposes only escaped, bounded data from the completed checker snapshot through stable member identities
and the versioned list, literal-search, and line-read operations. Both Python semantic rules remain deferred.

## Intended interface

The command will inspect exactly one path:

```sh
reproready check artifact.zip
reproready check artifact.zip --json
```

The Python API will return a checker-specific report type:

```python
from reproready import check_path

report = check_path("artifact.zip")
```

The implemented intake boundary holds a descriptor to the input, copies it into checker-owned temporary storage without following a top-level link, verifies that its identity, size, and modification state remain stable, and gives only the completed snapshot to one killable inspection child. The parent monitors the child against the fixed resident-memory and elapsed-time ceilings. If the source changes, the parent produces a valid small report with `snapshot_complete: false`, detected kind `unclassified`, a null SHA-256, `source_changed` issues, and error rule results. That represented input failure exits 0.

The internal intake classifies the supported direct forms, walks ZIP and ZIP64 containers plus nested
ZIPs through the fixed depth, and records every visited member in physical preorder. Duplicate names
retain separate ordinals and member IDs, so each occurrence remains addressable. After discovery, one
bounded source pass assigns dense global source IDs and parses each accepted Python file or supported
nbformat 4 code cell once. Its transient context binds each AST to the source and member IDs, physical
one-based notebook cell, AST line, and exact source syntax; only the public source record enters JSON.
Skipped, unsupported, and failed source-like members remain explicit, and parser metadata names only
parsers that ran. Every readable regular archive member receives one bounded full read. Bytes needed
for source parsing or nested-ZIP inspection are retained in checker-owned cache files, while opaque
member bytes stream to a discard sink. The implementation does not extract an archive directory tree.

The implemented `archive.structure` result maps exact intake facts to member-linked findings and
separate coverage blockers. Unsupported compression, encryption, links, special entries, unsafe
names, integrity and central-directory failures, and reached archive limits remain explicit.

## Supported inputs

| Input or content | v1 behavior |
|---|---|
| Direct `.py` | Supported Python source. |
| Direct nbformat 4 `.ipynb` | Supported when code-cell source and language metadata meet the ruleset contract. |
| Direct `requirements*.txt` | Supported dependency declarations. |
| Direct `pyproject.toml` | Supports PEP 621 `project.dependencies`. |
| ZIP and ZIP64 | Supported with fixed expansion, member, time, memory, and storage limits. |
| Nested ZIP | Supported through depth 3 beyond the outer ZIP. |
| R source and R metadata | Visible but unsupported. No R parser or R rule runs. |
| Tar, gzip, PDF, DOCX, executables, and other regular files | Produce a valid report naming the unsupported format. |
| Shell source, `setup.py`, Conda, Poetry, PDM, Pipenv, lock files, constraints, Dockerfiles, and requirement includes | Visible but not semantically analyzed. |
| Directories, top-level links, devices, sockets, and other non-regular paths | Rejected before content is read. |

The checker supports macOS and Linux. Other operating systems are rejected before the input is read. The score command retains its existing platform and input support.

Direct input classification uses the case-insensitive basename before ZIP detection: `.py`, `.ipynb`, `requirements*.txt`, and `pyproject.toml` are supported source forms; tar, gzip, PDF, and DOCX suffixes are unsupported forms; then the checker tests for ZIP; all remaining regular files are unsupported. This makes classification deterministic for mismatched names and prevents a DOCX container or a ZIP payload named `.py` from receiving unintended recursive inspection. See the [ruleset input table](checker-ruleset-v1.md#input-classification) for the exact precedence.

## Reports and limits

JSON reports follow schema version `1` and carry an independent tool version and ruleset version. Objects and lists use the ordering defined by the ruleset within one tool and runtime version. The contract does not promise canonical JSON bytes, report digests, or byte equality across Python runtimes and timeout boundaries.

Every report includes the artifact display basename, detected kind, byte size, snapshot state and SHA-256 when available, runtime and parser versions, effective limits, reached limits, member inventory, source index, and one result for every released rule. It never includes the source machine's absolute input path or a checker temporary path. Fixed issue messages do not interpolate raw exception text or artifact strings.

The fixed v1 limits are 2 GiB of input, 100,000 members, three nested ZIP levels, 512 MiB expanded per member, 4 GiB expanded in total, 8 MiB per Python or dependency file, 32 MiB per notebook, 1 GiB worker resident memory, 4 GiB temporary storage, 120 seconds, 10,000 observations, a 64 MiB JSON report, and 240 Unicode code points per observation snippet. The checker reserves one observation slot and 64 KiB of report space for reached-limit state and valid closing structure. Snippet truncation sets its own flag and does not make coverage partial. See the [ruleset](checker-ruleset-v1.md#fixed-limits) for exact counting, ordering, and truncation semantics.

Rule results use `complete`, `partial`, `unsupported`, `error`, or `not_applicable`. Findings are independent of coverage. Only a complete result with no observations may be summarized as `no finding in the checks run`; the checker never labels an artifact as passed.

The internal terminal renderer escapes control and bidirectional characters, treats Rich markup as literal text, labels findings and limitations without relying on color, and honors `NO_COLOR`. The future public command will use the same report-driven presentation rules.

## Process outcomes

| Exit | Meaning |
|---:|---|
| 0 | A valid report exists, including a report with findings, partial coverage, unsupported content, or a represented worker error. |
| 1 | A fatal internal failure prevents production of a valid report. |
| 2 | The invocation is invalid, the operating system is unsupported, or the top-level path is rejected. |

With `--json`, standard output will contain exactly one JSON object. Diagnostics will go to standard error. Findings and human-review observations do not change the exit status.

A source mutation, an input-size limit detected from metadata, or another expected parent-side intake failure uses exit 0 when the parent can emit a schema-valid technical report. Exit 1 is reserved for a failure that prevents such a report. The synthetic [`changed-during-snapshot.json`](../tests/fixtures/checker-report-v1/changed-during-snapshot.json) report fixes the mutation behavior.

## Worked synthetic example

Consider a synthetic ZIP containing this Python source and a declaration for `numpy`:

```python
import pandas

with open("/srv/project/data.csv") as handle:
    rows = handle.readlines()
```

The absolute-path rule records a `posix_absolute_path` finding at line 3 because the literal is argument 0 of `open`. It does not claim that the call executes or that `open` has its built-in binding. The dependency rule records `pandas` and `numpy` as separate evidence and emits human-review observations because their normalized names do not match. It does not call either package undeclared or unnecessary.

If a second Python member cannot be parsed, both Python rules remain useful for the inspected source but have `partial` status and identify the failed source. If inspection also reaches a nested-ZIP limit, the member remains in the inventory and the archive rule is partial. The synthetic [`partial-hostile-zip.json`](../tests/fixtures/checker-report-v1/partial-hostile-zip.json) report demonstrates these relationships, including escaped terminal-hostile text.

## Nonclaims

The checker does not establish executability, scientific correctness, reproducibility, policy compliance, or reviewer benefit. It does not execute code, install dependencies, access the network, infer package mappings from the current environment, or treat absent observations as proof that a condition is absent. A person remains responsible for decisions that depend on context beyond the exact static evidence.

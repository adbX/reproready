# Checker ruleset `python-v1`

The `python-v1` ruleset reports bounded static observations about one regular file. It does not execute artifact code, grade compliance, predict whether code will run, or establish that results are correct. Its three initial rules are emitted once each and in this order:

1. `archive.structure`
2. `python.absolute-path`
3. `python.dependencies`

The ruleset version identifies the rule catalogue and semantics. The separate report `schema_version` identifies the JSON shape. Adding a rule or changing a selector requires a new ruleset version; adding or changing a report field requires a new schema version.

## Shared coverage semantics

Every rule result has one status:

| Status | Meaning |
|---|---|
| `complete` | Every applicable input within the documented support boundary was inspected. Findings may still exist. |
| `partial` | At least one applicable input exists, but an applicable input was skipped, failed, or reached a limit. Successfully inspected inputs are not required. |
| `unsupported` | The top-level regular file cannot receive this rule under the v1 support contract. |
| `error` | A technical failure prevented a usable rule result. |
| `not_applicable` | Inspection completed, but no input to which the rule applies was present. |

`no finding in the checks run` is derived only from `complete` and an empty `observations` list. It is not a stored observation or a pass result. An observation is either a factual `finding` or a `needs_human_review` item. Neutral facts such as imports and dependency declarations are stored as evidence instead of being mislabeled as findings.

Skipped and failed inputs remain attached to each affected rule. A supported source that fails to decode or parse prevents that rule from reporting complete coverage. Unsupported source-like content remains in the member inventory or source index even when it does not affect an unrelated Python rule.

The JSON Schema enforces field shape, the three initial rule positions, and status relationships that are expressible in JSON Schema. The report producer also enforces unique IDs, no dangling member, source, or evidence references, matching parent and observation rule IDs, and no duplicate discovered rule IDs. `complete` has no skipped or failed inputs; `partial` has at least one; `unsupported` has a skipped top-level input and no evidence or observations; `error` has at least one failed input; and `not_applicable` has no evidence, observations, skipped inputs, or failed inputs.

## Input classification

An incomplete snapshot has detected kind `unclassified`; no content or extension classification is asserted. After a safe snapshot is complete, classification uses the case-insensitive display basename. The first matching row wins:

| Precedence | Condition | Detected kind |
|---:|---|---|
| 1 | Basename ends in `.py` | `direct_python` |
| 2 | Basename ends in `.ipynb` | `direct_notebook` |
| 3 | Basename matches `requirements*.txt` | `direct_requirements` |
| 4 | Basename is `pyproject.toml` | `direct_pyproject` |
| 5 | Basename ends in `.tar`, `.tar.gz`, or `.tgz` | `unsupported_tar` |
| 6 | Basename ends in `.gz` | `unsupported_gzip` |
| 7 | Basename ends in `.pdf` | `unsupported_pdf` |
| 8 | Basename ends in `.docx` | `unsupported_docx` |
| 9 | `zipfile.is_zipfile()` accepts the snapshot | `zip` |
| 10 | Any other regular file | `unsupported_regular` |

This precedence prevents a ZIP payload named as supported source from being expanded and prevents a DOCX container from being treated as a nested ZIP. Inside a ZIP, the same case-insensitive extension checks identify semantic source forms and formats that must remain inventory-only. A nested member is opened as ZIP only when its basename ends in `.zip` and the standard-library reader accepts it.

## Member and source identity

Identifiers are deterministic opaque ordinals, not paths:

- `container:0` is the outer ZIP. Each successfully opened nested ZIP receives the next container ID in physical discovery order.
- Every ZIP entry surfaced by the standard-library reader receives the next global `member:N` ID in physical central-directory order. Entries in a nested ZIP follow the member that contains that ZIP.
- `parent_member_id` is `null` for outer entries and points to the containing ZIP member for nested entries.
- Direct-file sources use `member_id: null`; the report's artifact identity locates the input.
- Duplicate ordinals are one-based among entries with the same decoded name in the same container.
- Python files, notebook documents and cells, requirements files, and `pyproject.toml` inputs receive global `source:N` IDs in member and then cell order.

Reports preserve each surfaced member's decoded name. If the standard-library reader cannot decode or finish the central directory, the report records an archive-level issue and whatever prefix the reader surfaced safely; it does not parse raw central-directory bytes to manufacture a member record. Reports do not extract archive entries or use artifact names as host paths.

The artifact display name is the final basename only. Fixed issue and coverage messages never interpolate source paths, temporary paths, raw exceptions, or artifact text. Artifact text appears only in the bounded `display_name`, member `name`, evidence `value`, and observation `snippet` fields.

Lists have these deterministic orders:

- Rule results follow ruleset order.
- Members, containers, sources, and their numeric IDs follow the discovery order above.
- Parser records sort by parser name; reached limits follow the fixed-limit table.
- Issues sort by top-level before member, then numeric member ID, condition code, and message template.
- Within a rule, evidence and observations sort by numeric source ID, numeric member ID, cell, line, condition or evidence kind, and value. `null` locations sort first.
- Skipped and failed inputs sort by numeric source ID, numeric member ID, reason code, and message template.

JSON object fields follow schema declaration order. Serialization uses compact UTF-8 JSON for limit accounting and rejects non-finite numbers; presentation mode may add indentation without changing list order.

## Fixed limits

The checker has no user-adjustable resource profile in v1. Every report repeats these values and names every reached resource limit:

| Limit | Value |
|---|---:|
| Input bytes | 2 GiB |
| ZIP members across all containers | 100,000 |
| Nested ZIP depth beyond the outer ZIP | 3 |
| Expanded bytes for one member | 512 MiB |
| Expanded bytes across the artifact | 4 GiB |
| One Python source | 8 MiB |
| One notebook document | 32 MiB |
| One dependency file | 8 MiB |
| Worker resident memory | 1 GiB |
| Checker-owned temporary storage | 4 GiB |
| Inspection elapsed time | 120 seconds |
| Observations | 10,000 |
| Encoded JSON report | 64 MiB |
| Observation snippet display bound | 240 Unicode code points |

The outer ZIP is depth 0. A ZIP member opened from it is depth 1. A member beyond depth 3 is inventoried but not opened. Expanded-byte accounting uses bytes actually read after decompression. Parser limits are checked before decoding or parsing the complete body. The member, source, evidence, observation, skipped-input, and failed-input arrays retain deterministic prefixes when a resource limit stops inspection.

The snippet display bound is not a coverage or resource limit. A longer available snippet is cut to 240 Unicode code points and sets `snippet_truncated: true`; the source can still have `complete` rule coverage. `max_snippet_codepoints` appears in `effective` but never in `reached`.

The checker reserves one observation slot and 64 KiB of encoded report space for reached-limit state and closing JSON structure. It stops ordinary observations at 9,999 and emits `resource_limit_reached` under the affected rule as the 10,000th. Before appending any other source, issue, evidence, observation, skipped-input, or failed-input record, it measures the compact UTF-8 encoding with the reserved closing structure; if that append would cross 64 MiB, it omits the record, marks `max_report_bytes` reached, emits `resource_limit_reached` under the affected rule, and stops inspection. The resulting report identifies the first omitted member or source when known. Reaching a limit makes affected coverage partial unless no usable rule result can be produced, in which case the parent emits a small technical error report when possible.

## `archive.structure`

This rule applies only to ZIP and ZIP64 inputs. Direct supported files return `not_applicable`; unsupported regular files return `unsupported`.

The rule emits exact structural observations for:

| Condition code | Exact condition |
|---|---|
| `absolute_member_path` | A decoded member name starts with `/`; starts with a drive letter, colon, and slash or backslash; starts with two slashes or backslashes; or starts with a Windows device prefix such as `\\?\` or `\\.\`. |
| `parent_path_segment` | Splitting the decoded name on both `/` and `\` produces a segment exactly equal to `..`. |
| `control_character_in_name` | The decoded name contains U+0000-U+001F or U+007F. |
| `duplicate_member_name` | Two or more central-directory entries in one container have exactly the same decoded name. Each entry remains separately addressable. |
| `symlink_entry` | Unix mode bits in `ZipInfo.external_attr` identify a symbolic link. The checker does not follow it. |
| `special_entry` | Nonzero Unix file-type bits identify an entry that is neither a regular file, directory, nor symbolic link. |
| `encrypted_entry` | General-purpose flag bit 0 is set. The checker does not request or guess a password. |
| `unsupported_compression` | The running standard-library ZIP reader does not support the entry's compression method. |
| `integrity_error` | Reading an entry reports a CRC, truncation, malformed structure, or other ZIP integrity failure. |
| `central_directory_error` | The standard-library reader cannot decode or complete the ZIP central directory. This archive-level observation has no member ID. |
| `resource_limit_reached` | A fixed member, nesting, expansion, memory, storage, time, observation, or report limit prevents complete inspection. |

Every reached archive limit emits `resource_limit_reached` in addition to the specific inventory issue. The Python rules use the same condition code when their own inspection becomes incomplete because of a limit. These observations describe ZIP metadata and reads only. They do not claim that extraction occurred or that a name behaves identically on every filesystem. Directory entries are identified by the standard-library ZIP directory predicate. Other unsupported member formats remain inventoried but are not recursively parsed.

## `python.absolute-path`

This rule applies to Python files and supported notebook code cells that parse successfully with the running interpreter's `ast` module. It inspects only `ast.Constant` string values used in the call positions below. The callee match is lexical: it describes the spelling in the syntax tree and does not claim that a name was not rebound.

| Callee spelling | Supported argument positions |
|---|---|
| `open`, `io.open` | positional 0 or keyword `file` |
| `os.chdir`, `os.listdir`, `os.scandir`, `os.mkdir`, `os.remove`, `os.unlink`, `os.rmdir`, `os.stat`, `os.lstat`, `os.access` | positional 0 or keyword `path` |
| `os.makedirs` | positional 0 or keyword `name` |
| `os.rename`, `os.replace` | positional 0 or keyword `src`; positional 1 or keyword `dst` |
| `os.walk` | positional 0 or keyword `top` |
| `os.path.exists`, `os.path.isfile`, `os.path.isdir`, `os.path.getsize` | positional 0 or keyword `path` |
| `Path`, `PurePath`, `PosixPath`, `PurePosixPath`, `WindowsPath`, `PureWindowsPath` and the same names qualified by `pathlib` | positional 0 |

A decoded literal receives one of these syntax classes, checked in the listed order:

1. `windows_device_path`: starts with `\\?\` or `\\.\`.
2. `windows_unc_path`: starts with two slashes or two backslashes followed by a non-separator.
3. `windows_drive_path`: starts with an ASCII letter, colon, and slash or backslash.
4. `posix_absolute_path`: starts with `/`.
5. `tilde_path`: is exactly `~`, starts with `~/` or `~\`, or starts with `~name/` or `~name\` for a nonempty name.

The observation contains the source member, one-based notebook cell when applicable, one-based source line, source syntax snippet, and decoded syntax class. The rule does not perform constant propagation, inspect strings outside the listed call positions, resolve aliases, determine reachability, or interpret paths with the host operating system.

Notebook support is limited to nbformat 4 documents whose code-cell `source` is a string or a list of strings. The checker parses code-cell source only. Malformed JSON, unsupported notebook versions, conflicting or non-Python language metadata, magics, shell escapes, and mixed-language cells remain explicit partial or unsupported source records.

## `python.dependencies`

This rule records four evidence classes separately:

1. Imports from successfully parsed `ast.Import` and absolute `ast.ImportFrom` nodes. `import a.b` and `from a.b import c` produce the top-level name `a`. Relative imports are local-module evidence.
2. Direct declarations from case-insensitive basenames matching `requirements*.txt`.
3. Direct declaration strings from the PEP 621 `project.dependencies` array in `pyproject.toml`.
4. Running-runtime standard-library names and obvious local modules used to classify imports.

A supported requirements line is one physical line containing a distribution name, optional extras, and optional version specifiers. Blank lines and comments are ignored. Includes, constraints, options, editable installs, local paths, URLs, VCS references, continuations, and environment markers remain visible but unsupported. For PEP 621, `project.dependencies` must be an array of strings. Optional dependency groups, tool-specific tables, direct URLs, and environment-marker semantics remain visible but unsupported.

Distribution and import names are normalized by lowercasing and replacing each run of `-`, `_`, or `.` with `-`. Only identical normalized names are exact matches. The checker does not maintain an import-to-distribution map and does not inspect installed packages.

Matching never crosses a ZIP container or inferred project root. A supported dependency file defines a project root at its parent directory. A Python source belongs to the deepest dependency-file ancestor in the same container; if no such ancestor exists, it belongs to the container root after removing one directory component shared by every file. When several dependency files define the same root, their declarations are combined in member order. Imports and declarations in different nested ZIPs or different inferred roots remain separate evidence.

An obvious local module is a matching `name.py` or `name/__init__.py` beneath the current inferred project root or its direct `src/` child. The search never crosses into another inferred project root or ZIP container. Other local-module layouts remain unclassified.

An import that is neither a running-runtime standard-library name, an obvious local module, nor an exact declaration match produces `needs_human_review`. A declaration without an exact import match also produces `needs_human_review`; it may be a valid tool, plugin, optional, or transitive dependency. These observations ask a person to interpret non-identical evidence and do not claim that a dependency is missing.

Poetry, PDM, Pipenv, Conda, lock files, Dockerfiles, constraints, requirement includes, namespace-package inference, optional groups, and `setup.py` remain unsupported in this ruleset. Their presence is retained in inventory and makes dependency coverage partial when the file is source-like for dependency review.

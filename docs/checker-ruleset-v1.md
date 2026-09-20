# Checker ruleset `python-v1`

The `python-v1` ruleset defines eleven checks for the [ReproReady checker](checker.md). Each report includes one result per rule, in the order of the rule sections below. Observations describe files, not whether code runs, results reproduce, or an artifact meets a policy.

The ruleset version identifies the rules and their matching conditions; `schema_version` identifies the JSON format. Adding a rule or changing a matching condition requires a new ruleset version. Adding or changing a report field requires a new schema version.

## Shared coverage semantics

Each rule has one coverage status. Coverage describes what was inspected, not whether the artifact passes.

| Status | Meaning |
|---|---|
| `complete` | Every applicable supported input was inspected; none was skipped or failed. Findings may still exist. |
| `partial` | At least one applicable input was skipped, failed, or reached a limit. No successful inspection is required. |
| `unsupported` | The top-level regular file is unsupported for this rule. It is recorded as skipped, with no evidence or observations. |
| `error` | A technical failure prevented a usable result; at least one input is recorded as failed. |
| `not_applicable` | Inspection completed with no applicable input, evidence, observations, skipped inputs, or failed inputs. |

`no finding in the checks run` means `complete` with an empty `observations` list. It is neither a stored observation nor a pass. A `finding` records a static fact; `needs_human_review` asks for interpretation. Neutral facts, such as imports and dependency declarations, are evidence rather than findings.

Skipped and failed inputs stay attached to each affected rule. Decode or parse failures in supported sources prevent complete coverage. Unsupported source-like content remains in the inventory or source index without necessarily affecting unrelated Python rules.

The JSON Schema enforces field types, rule positions, and the status constraints it can represent. The producer and saved-report viewer also enforce unique member and source IDs, valid inventory, parent, source, observation, and evidence links, matching observation ownership, and no duplicate discovered rule IDs.

## Input classification

Incomplete snapshots remain `unclassified`. Complete snapshots use the case-insensitive display basename; the first matching row wins:

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

Source suffixes take precedence over ZIP detection, and DOCX containers are not treated as ZIPs. The same extension checks classify sources and inventory-only formats inside ZIPs. A nested member is opened as ZIP only if its basename ends in `.zip` and the standard-library reader accepts it.

## Member and source identity

IDs are deterministic ordinals, not paths:

- `container:0` is the outer ZIP. Successfully opened nested ZIPs receive successive IDs in physical discovery order.
- Every entry returned by the standard-library reader receives a global `member:N` ID in physical central-directory order. Nested entries follow their containing ZIP member.
- `parent_member_id` is `null` for outer entries; nested entries point to their containing ZIP member.
- Direct sources use `member_id: null`; the report's artifact identity locates the input.
- Duplicate ordinals start at one for each decoded name within a container.
- Python files, notebook documents and cells, requirements files, and `pyproject.toml` inputs receive global `source:N` IDs in member, then cell order.

Reports preserve decoded member names. If the reader cannot decode or finish the central directory, the report records an archive-level issue and any safely returned prefix. It does not construct records from raw central-directory bytes, extract entries, or use artifact names as host paths.

The artifact display name is its basename. Fixed issue and coverage messages exclude source paths, temporary paths, raw exceptions, and artifact text. Artifact text appears only in the bounded `display_name`, member `name`, evidence `value`, and observation `snippet` fields.

Lists have these deterministic orders:

- Rule results follow ruleset order.
- Members, containers, sources, and their numeric IDs follow the discovery order above.
- Parser records sort by parser name; reached limits follow the fixed-limit table.
- Issues sort by top-level before member, then numeric member ID, condition code, and message template.
- Within a rule, evidence and observations sort by numeric source ID, numeric member ID, cell, line, condition or evidence kind, and value. `null` locations sort first.
- Skipped and failed inputs sort by numeric source ID, numeric member ID, reason code, and message template.

JSON fields follow schema declaration order. Limit accounting uses compact UTF-8 JSON and rejects non-finite numbers. Indentation may change; list order does not.

For every file selected by `reproready view`, the viewer applies a separate 128 MiB bound, accepts compact or pretty-printed JSON under that bound, and applies the packaged schema and the reference checks above without reopening the artifact. Selecting several files or a shallow report directory does not change these per-report admission rules.

## Fixed limits

Limits are fixed in v1. Every report includes their values and identifies those reached:

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

The outer ZIP is depth 0; its nested ZIPs are depth 1. Members beyond depth 3 are inventoried but not opened. Expansion counts actual decompressed bytes read. Parser limits apply before decoding or parsing a complete body. On reaching a resource limit, member, source, evidence, observation, skipped-input, and failed-input arrays retain deterministic prefixes.

Snippet truncation does not reduce coverage: text beyond 240 Unicode code points is cut and sets `snippet_truncated: true`. `max_snippet_codepoints` appears in `effective`, never `reached`.

The checker reserves one observation slot and 64 KiB of report space for limit reporting and closing JSON. After 9,999 ordinary observations, the 10,000th is `resource_limit_reached` under the affected rule.

Before adding any source, issue, evidence, observation, skipped-input, or failed-input record, the checker measures compact UTF-8 size including the closing reserve. An append exceeding 64 MiB is omitted: inspection stops, `max_report_bytes` is marked reached, and the affected rule emits `resource_limit_reached`. The report identifies the first omitted member or source when known.

Reached limits make affected coverage partial. If no usable rule result is possible, the parent instead emits a small technical error report when possible.

## `archive.structure`

Checks ZIP and ZIP64 inputs. Supported direct files return `not_applicable`; unsupported regular files return `unsupported`.

Structural observations use these condition codes:

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

Each reached archive limit emits `resource_limit_reached` alongside its inventory issue. Python rules use the same code for limits affecting their inspection. These observations describe metadata and reads, not extraction or filesystem behavior. Directories follow the standard-library ZIP predicate. Other unsupported member formats are inventoried, not recursively parsed.

## `python.absolute-path`

Checks Python files and supported notebook cells parsed by the running interpreter's `ast` module. Only `ast.Constant` strings in these call positions are inspected. Callees match by spelling, even if a name was rebound:

| Callee spelling | Supported argument positions |
|---|---|
| `open`, `io.open` | positional 0 or keyword `file` |
| `os.chdir`, `os.listdir`, `os.scandir`, `os.mkdir`, `os.remove`, `os.unlink`, `os.rmdir`, `os.stat`, `os.lstat`, `os.access` | positional 0 or keyword `path` |
| `os.makedirs` | positional 0 or keyword `name` |
| `os.rename`, `os.replace` | positional 0 or keyword `src`; positional 1 or keyword `dst` |
| `os.walk` | positional 0 or keyword `top` |
| `os.path.exists`, `os.path.isfile`, `os.path.isdir`, `os.path.getsize` | positional 0 or keyword `path` |
| `Path`, `PurePath`, `PosixPath`, `PurePosixPath`, `WindowsPath`, `PureWindowsPath` and the same names qualified by `pathlib` | positional 0 |

Decoded literals receive the first matching syntax class:

1. `windows_device_path`: starts with `\\?\` or `\\.\`.
2. `windows_unc_path`: starts with two slashes or two backslashes followed by a non-separator.
3. `windows_drive_path`: starts with an ASCII letter, colon, and slash or backslash.
4. `posix_absolute_path`: starts with `/`.
5. `tilde_path`: is exactly `~`, starts with `~/` or `~\`, or starts with `~name/` or `~name\` for a nonempty name.

Observations include the source member, one-based notebook cell when applicable, one-based line, syntax snippet, and decoded syntax class. The rule does not propagate constants, inspect other call positions, resolve aliases, determine reachability, or interpret paths using the host OS.

Supported notebooks use nbformat 4 with code-cell `source` as a string or list of strings. Only code-cell source is parsed. Malformed JSON, other notebook versions, conflicting or non-Python language metadata, magics, shell escapes, and mixed-language cells remain explicit partial or unsupported content.

## `python.dependencies`

Records four evidence classes:

1. Imports from parsed `ast.Import` and absolute `ast.ImportFrom` nodes. `import a.b` and `from a.b import c` yield `a`; relative imports are local-module evidence.
2. Direct declarations from case-insensitive `requirements*.txt` basenames.
3. Strings in the PEP 621 `project.dependencies` array in `pyproject.toml`.
4. Standard-library names from the running interpreter and obvious local modules.

Supported requirements entries occupy one physical line: a distribution name, optional extras, and optional version specifiers. Blank lines and comments are ignored. Includes, constraints, options, editable installs, local paths, URLs, VCS references, continuations, and environment markers remain visible but unsupported.

PEP 621 `project.dependencies` must be an array of strings. Optional groups, tool-specific tables, direct URLs, and environment-marker semantics remain visible but unsupported.

Names are lowercased, with each run of `-`, `_`, or `.` replaced by `-`. Only identical normalized import and distribution names match. The checker neither maps imports to distributions nor inspects installed packages.

Matching stays within a ZIP container and inferred project root. Each supported dependency file defines a root at its parent directory. Sources use the deepest dependency-file ancestor in their container. Without one, they use the container root after removing one directory component shared by every file. Declarations for the same root combine in member order.

An obvious local module matches `name.py` or `name/__init__.py` under that root or its direct `src/` child. Matching never crosses into another project root or ZIP container; other layouts remain unclassified.

An import outside the standard library without a local-module or exact declaration match produces `needs_human_review`. So does a declaration without an exact import match: it may describe a tool, plugin, optional, or transitive dependency. Neither observation establishes a missing dependency.

Poetry, PDM, Pipenv, Conda, lock files, Dockerfiles, constraints, requirement includes, namespace-package inference, optional groups, and `setup.py` are unsupported. These files remain inventoried and make coverage partial when recognized as dependency-related inputs.

## `python.sys-path-three-dot`

Checks parsed `.py` files for unchanged bindings from `from sys import path` or `from sys import path as alias`. It selects `alias.append(literal)` with one positional argument and no keywords. Splitting the decoded literal on `/` and `\` must yield a segment exactly `...` to emit `three_dot_path_segment`.

Excludes `..`, four dots, substrings, computed values, `import sys; sys.path.append(...)`, notebooks, and rebound aliases. The question is whether `...` names an intentional directory or assumes an artifact-specific path. The checker does not inspect the filesystem, infer reachability, or declare the path invalid.

## `python.download-comment-http-url`

Checks tokenizable `.py` files and supported notebook code cells. A `tokenize` `COMMENT` token emits `download_comment_with_http_url` if its decoded physical line contains both the case-insensitive whole word `download` and a URL with scheme `http` or `https`. URL recognition excludes trailing sentence punctuation; the bounded snippet covers the complete comment line.

Strings, Markdown/raw cells, outputs, split-line instructions, non-HTTP locations, and comments missing either element do not match. Tokenization is independent of AST parsing, so coverage can be complete while another Python rule is partial. The question is whether the comment describes manual acquisition, an optional operation, or background information.

## `python.open-bundled-archive-member`

Checks parsed `.py` files and supported notebook cells inside ZIPs. It selects unshadowed builtin `open()` calls whose path is a supported relative POSIX literal or a simple name bound earlier to an unreassigned literal in the same module or ordered notebook namespace.

Omitted mode means read mode. A literal mode must contain `r`, exclude `w`, `a`, and `x`, and be a valid text, binary, or update-mode combination.

The normalized archive-root-relative path must be absent from the source's container and identify exactly one readable regular member in a descendant ZIP. `read_path_only_in_bundled_archive` links to one `bundled_archive_member` evidence record.

Zero, duplicate, unreadable, unrelated-container, wrapper-directory, dynamically computed, explicit archive-reader, and incomplete-inventory cases do not match. The question is whether the runtime makes the nested member available at that path. The relationship does not establish extraction, mounting, call reachability, or earlier calls' effects.

## `python.pandas-csv-inventory-absence`

Checks parsed `.py` members only when the artifact inventory is complete, with no skipped discovery or listing limit. It selects `alias.read_csv(path)` through an unchanged `import pandas` binding. The first positional argument must be a supported relative POSIX literal or a name assigned to one exactly once earlier at module scope.

The rule compares the normalized literal case-sensitively with archive-root-relative and source-parent-relative locations in the source's container. It emits `pandas_csv_not_in_inventory` only if neither location is represented.

Direct `.py` inputs, notebooks, `from pandas import read_csv`, keyword-only operands, constructed paths, function parameters, URIs, other readers, and incomplete inventories do not match.

The question is whether a documented step, mount, generation process, or runtime supplies the path. The checker does not search the host filesystem, cross containers, match similarly named files with other extensions, or establish that runtime data is missing.

## `python.notebook-pip-install`

Checks reconstructable code-cell source in supported nbformat 4 Python notebooks. One-based logical lines starting with `!` after whitespace are parsed with `shlex` in POSIX mode. The first two literal tokens must be `pip` and `install` to emit `notebook_pip_install`. The bounded snippet covers the complete logical line.

`%pip`, `python -m pip`, subprocess calls, comments, computed commands, Markdown cells, `.py` files, and other package managers do not match. Unterminated shell quotes or continuations produce `notebook_shell_parse_error`. The command is not dependency evidence.

The question is whether the command is required setup, optional convenience, or historical material. The checker neither runs it nor treats its operands as a complete environment declaration.

## `python.gdown-anonymized-value`

Checks parsed `.py` modules and supported notebook cells in document order. A nonempty dictionary literal must be assigned to a simple module or notebook-global name, with explicit entries whose values are all exactly `ANONYMIZED` (case-sensitive). Unpacking, mixed values, mutation, rebinding, or an unavailable intervening cell invalidates the relation.

An unchanged direct `import gdown` binding must call `.download()` with a qualifying dictionary subscript in its first positional argument, directly, in an f-string, or in string concatenation. One intervening simple-name assignment in the same straight-line lexical scope is supported. Propagation does not cross branches or loops, follow helpers, aliases, returns, files, or environment values, or exceed one assignment edge.

`anonymized_download_identifier` links to `anonymized_mapping_value` evidence at the first value literal, reused for calls through the same unchanged mapping. The question is whether anonymization is intentional or another release step supplies an identifier, not whether the call executes or a download fails.

## `python.entry-point-input`

Checks parsed `.py` files and supported notebook cells for an unshadowed builtin `input()` call within a module-level `if __name__ == "__main__":` guard. Matching descends through expressions and control statements, not nested function, class, or lambda bodies defined in the guard.

Reversed comparisons, membership tests, nonliteral sentinels, functions called by the guard, `sys.stdin`, and contexts that lexically bind `input` do not match. An unavailable intervening notebook cell invalidates prior tracked bindings. `entry_point_stdin` locates the call.

The question is how stdin is supplied. Redirected or piped input is valid; the observation does not imply a person must type a value.

## `python.gfile-bucket-authority`

Checks parsed `.py` files using unchanged direct imports from `tensorflow.io.gfile`, qualified `tensorflow.io.gfile.GFile` references, or their direct aliases. Supported joins include direct `os.path.join`, `os` aliases, `from os import path` aliases, and `from os.path import join` aliases.

The first positional `GFile` argument must be a decoded literal or supported join expression starting with one. URI parsing must find scheme `gs` and authority exactly `bucket` (case-sensitive) to emit `bucket_authority_literal`.

Other authorities, credentials, ports, uppercase or suffixed authorities, other schemes, keyword-only operands, formatting, concatenation, reassignment, wrappers, computed paths, and notebooks do not match.

The question is whether `bucket` names a real Google Cloud Storage bucket or needs artifact-specific interpretation. It is a legal authority, not necessarily a placeholder. No network check occurs.

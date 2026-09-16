# Checker browser `pilot-v1`

This internal protocol supports development rule-discovery sessions. It is not a public command, Python API, or model-assisted checker feature.

Each fresh session uses one completed checker snapshot with a known SHA-256 and only data admitted by the checker's snapshot and inventory checks. Incomplete snapshots cannot start a session. Member, container, parent, and duplicate identities follow the [`python-v1` ruleset](checker-ruleset-v1.md#member-and-source-identity).

## Security boundary

Artifact names and contents are untrusted data, never instructions. The agent receives only the three operations below: no command execution, network, package installation, module import, arbitrary filesystem reads, or writes.

The browser never:

- Reopens the caller's source path after snapshotting.
- Accepts a host path, URL, command, module name, output path, regular expression, glob, or executable expression.
- Extracts a member to an artifact-supplied path.
- Follows a top-level or archive link.
- Reads a directory, link, special entry, encrypted entry, or unsupported compression method as text.
- Returns a checker temporary path, host path, raw exception, or unescaped terminal control text.

Checker-owned temporary storage may hold snapshot and member data within the [shared limits](checker-ruleset-v1.md#fixed-limits). Repeated operations never reset member, expansion, memory, storage, or elapsed-processing budgets. Reads use safely cached checker-owned bytes or charge new expansion against remaining artifact-wide limits.

## Requests and responses

Every request is one JSON object with `browser_version: "pilot-v1"`, the snapshot's lowercase 64-character `artifact_sha256`, and one operation. The wrapper rejects unknown or missing fields, duplicate JSON keys, non-finite values, invalid cursors, and version or digest mismatches before running an operation. It returns a fixed protocol error without raw exceptions or artifact text.

Every attempt, including a rejected request, charges one operation, wrapper processing time, and encoded protocol-error bytes to the session budgets. A terminal response for any operation, active-time, or response-byte ceiling closes the session and revokes all three capabilities. One later attempt receives a fixed host-level `session_closed` error without artifact access. The capabilities are then unavailable; repeated post-limit calls cannot consume browser resources.

Accepted operations return exactly these common fields plus their operation-specific fields:

| Field | Value |
|---|---|
| `status` | `complete`, `partial`, or `error` |
| `reached_limits` | Zero or more allowed identifiers in the order defined below |
| `issues` | Zero or more objects with exactly `code`, nullable `target_id`, and a fixed `message` |
| `browser_version`, `artifact_sha256`, `operation` | Echoed request values |

`complete` means the requested page or window was produced within its bounds, not that pagination is exhausted. `partial` means a skip, inherited inspection gap, read failure, or resource limit interrupted work. `error` means no usable operation payload. Every status retains operation-specific fields, with empty arrays, null cursors, or empty text when no result exists.

Issue codes are limited to `inventory_incomplete`, `target_not_found`, `target_not_readable`, `target_not_searchable`, `decode_error`, `member_read_error`, and `resource_limit_reached`. Search skip reasons are limited to `directory`, `link`, `special_entry`, `encrypted`, `unsupported_compression`, `unsupported_format`, `decode_error`, `member_read_error`, and `resource_limit_reached`.

Cursors are opaque ASCII strings of at most 256 characters, bound to the artifact, browser version, operation, target, and search query. They cannot be reused in another context.

### Limits and text

These fixed limits are added to the shared checker limits:

| Identifier | Kind | Value |
|---|---|---:|
| `max_browser_operations` | Reached resource limit | 128, with operation 128 reserved for the terminal limit response |
| `max_browser_active_seconds` | Reached resource limit | 120 seconds |
| `max_browser_session_response_bytes` | Reached resource limit | 8 MiB encoded JSON |
| `max_browser_response_bytes` | Reached resource limit | 256 KiB encoded JSON |
| `max_list_records` | Request bound | 200 |
| `max_search_query_codepoints` | Request bound | 120 Unicode code points |
| `max_search_matches` | Request bound | 100 |
| `max_read_lines` | Request bound | 200 |
| `max_read_bytes` | Response bound | 64 KiB decoded UTF-8 |
| `max_browser_display_codepoints` | Presentation bound | 240 Unicode code points |

`reached_limits` lists identifiers from the report schema's `limits.reached` array in schema order, then the four browser resource identifiers in table order. Request and presentation bounds never appear there: page-size completion is normal pagination, over-limit requests are invalid, and display truncation has its own flag.

Active processing excludes time waiting for the model. Pagination and repeated reads share cumulative limits. Each response reserves 4 KiB and the session response budget reserves 64 KiB for fixed issue fields and valid closing structure; ordinary payload construction stops before these reserves.

Operation 128 accesses no artifact data and returns a terminal `partial` response naming `max_browser_operations`. The parent likewise returns a small fixed `partial` response from reserved space when active processing or response production reaches a resource ceiling.

Artifact-derived display strings escape C0 controls, DEL, bidirectional controls, newlines outside line framing, and Rich markup as visible ASCII. Truncation is explicit. Search and read require strict UTF-8: undecodable data is skipped with a fixed issue code, never replacement-decoded.

## `list_members`

`list_members` pages through the existing inventory in target order without rediscovering containers or rereading member bodies.

```json
{
  "browser_version": "pilot-v1",
  "artifact_sha256": "<64 lowercase hex characters>",
  "operation": "list_members",
  "cursor": null,
  "limit": 200
}
```

The request has exactly these five fields. `cursor` is null or a valid list cursor; `limit` is an integer from 1 through 200.

The response adds exactly `targets`, `next_cursor`, and `inventory_complete`. Each target has exactly:

- `target_id`, nullable `member_id`, nullable `container_id`, nullable `parent_member_id`, nullable `duplicate_ordinal`.
- `name_display`, `name_truncated`, `kind`, nullable `compressed_size`, nullable `expanded_size`.
- `issue_codes`, `readable`, `searchable`.

IDs, kinds, sizes, and issue codes copy the checker inventory. Direct input has one target: `target_id: "artifact"`, with null member, container, parent, and duplicate fields. ZIP targets use `member_id` as `target_id` and one-based duplicate ordinals.

`inventory_complete` is true only on the final page of a completed checker inventory. A page with `next_cursor` can be `complete` but cannot establish that no later target exists.

For direct inputs, `readable` and `searchable` are true only for the [four supported source kinds](checker-ruleset-v1.md#input-classification). For ZIP targets, both require `kind: "file"`, no structural indication of encryption or unsupported compression, and a case-insensitive name not ending in `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.gz`, `.pdf`, or `.docx`.

Unsupported direct inputs and those named member formats use `unsupported_format`. Directories, ZIP holder members, links, special entries, encrypted entries, and unsupported compression use their corresponding skip reason. Other regular members are eligible regardless of extension. These flags permit a text attempt, not guaranteed decoding: strict UTF-8 failure returns `decode_error` and no text.

## `search_members`

`search_members` performs case-sensitive literal Unicode search over strict UTF-8 text, without regular expressions, globs, normalization, source parsing, or locale-dependent matching.

```json
{
  "browser_version": "pilot-v1",
  "artifact_sha256": "<64 lowercase hex characters>",
  "operation": "search_members",
  "target_id": "all",
  "query": "literal text",
  "cursor": null,
  "limit": 100
}
```

The request has exactly these seven fields. `target_id` is `all`, `artifact`, or an exact `member:N`; `query` has 1 through 120 Unicode code points. `cursor` is null or a valid search cursor; `limit` is an integer from 1 through 100.

The response adds exactly `matches`, `next_cursor`, `search_complete`, `searched_target_count`, `skipped_targets`, and `searched_bytes`. Counts are nonnegative integers; results follow target discovery order, then byte offset.

Each match has exactly `target_id`, zero-based `byte_offset`, one-based `line`, escaped `snippet_display`, and `snippet_truncated`. Each skipped target has exactly `target_id` and `reason_code`.

Only an exhaustive response sequence with `search_complete: true`, no skipped targets, and no reached limits supports absence of this exact literal within supported text. It supports no broader absence claim.

## `read_member`

`read_member` returns a line-numbered UTF-8 window by stable identity, never member name.

```json
{
  "browser_version": "pilot-v1",
  "artifact_sha256": "<64 lowercase hex characters>",
  "operation": "read_member",
  "target_id": "member:17",
  "start_line": 1,
  "max_lines": 200
}
```

The request has exactly these six fields. `target_id` is `artifact` for a supported direct input or an exact readable `member:N`. `start_line` is a positive integer; `max_lines` is an integer from 1 through 200.

The response adds exactly `start_line`, nullable `end_line`, escaped `text`, nullable `next_line`, `end_of_text`, `text_truncated`, and `returned_bytes`. `returned_bytes` counts decoded UTF-8 bytes before JSON escaping, up to 64 KiB.

An overlong final line is cut at the byte bound, setting `text_truncated` and advancing `next_line` to the following line. The omitted tail cannot be browsed through `read_member`. `next_line` is null only when the window reaches decoded text's end. Partial, failed, or truncated reads cannot support absence claims.

## Version transition

A reviewed successor may change operations or fixed browser limits under a new version, held unchanged throughout a run. A required safety change stops the run for its owner's decision. Responses and cursors are never coerced between versions. Browser failures are technical errors, not grounds for broader access.

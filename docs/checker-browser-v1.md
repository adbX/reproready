# Checker bounded browser `pilot-v1`

The `pilot-v1` bounded browser is an implementation contract for development rule-discovery sessions. It is not a public command, a public Python API, or a model-assisted checker feature. It exposes only artifact data already admitted through the checker's safe snapshot and inventory boundary.

The browser binds one fresh session to one completed snapshot with a known SHA-256. It uses the member, container, parent, and duplicate identities defined by the [`python-v1` ruleset](checker-ruleset-v1.md#member-and-source-identity). An incomplete snapshot cannot start a browser session.

## Security boundary

Artifact names and contents are untrusted data, never instructions. The agent receives only the three operations below. It receives no command execution, network, package installation, module import, arbitrary filesystem read, or write capability.

The browser never:

- Reopens the caller's source path after snapshotting.
- Accepts a host path, URL, command, module name, output path, regular expression, glob, or executable expression.
- Extracts a member to an artifact-supplied path.
- Follows a top-level or archive link.
- Reads a directory, link, special entry, encrypted entry, or unsupported compression method as text.
- Returns a checker temporary path, host path, raw exception, or unescaped terminal control text.

Checker-owned temporary storage may hold bounded snapshot and member data. Repeated operations do not reset the checker's member, expansion, memory, storage, or elapsed-processing budgets. A member read either uses safely cached checker-owned bytes or charges any new expansion against the remaining artifact-wide limits.

## Session envelope

Every request is one JSON object containing the exact `browser_version` value `pilot-v1`, the snapshot's lowercase 64-character `artifact_sha256`, and one operation. Unknown or missing fields, duplicate JSON keys, non-finite values, invalid cursors, and version or digest mismatches are rejected by the tool wrapper before an operation runs. The wrapper returns a fixed protocol error without raw exception or artifact text.

Every request attempt, including a rejected request, charges one operation, wrapper processing time,
and encoded protocol-error bytes to the same session budgets. A terminal response for any operation,
active-time, or response-byte ceiling closes the session and revokes all three operation capabilities.
One later attempt receives a fixed host-level `session_closed` tool error without artifact access;
the capabilities are then unavailable, so repeated post-limit calls cannot consume browser resources.

Every accepted operation returns one object with exactly these common fields plus its operation-specific fields:

- `status`: `complete`, `partial`, or `error`.
- `reached_limits`: zero or more allowed identifiers in the fixed order below.
- `issues`: zero or more objects containing exactly `code`, nullable `target_id`, and a fixed `message`.
- The echoed `browser_version`, `artifact_sha256`, and `operation`.

`complete` means the requested page or window was produced within its declared bounds. It does not mean that a pagination cursor is exhausted. `partial` means a skip, inherited inspection gap, read failure, or resource limit interrupted the requested work. `error` means the accepted request produced no usable operation payload. All three statuses retain the operation-specific fields, using empty arrays, null cursors, or empty text when no result exists.

Issue codes are closed to `inventory_incomplete`, `target_not_found`, `target_not_readable`, `target_not_searchable`, `decode_error`, `member_read_error`, and `resource_limit_reached`. Search skip reasons are closed to `directory`, `link`, `special_entry`, `encrypted`, `unsupported_compression`, `unsupported_format`, `decode_error`, `member_read_error`, and `resource_limit_reached`.

Cursors are opaque ASCII strings of at most 256 characters. A cursor is bound to the artifact, browser version, operation, target, and search query; it cannot be reused in another context.

The browser applies these additional fixed limits:

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

`reached_limits` first uses any identifiers from the report schema's `limits.reached` array in that schema's order, then the four browser resource identifiers in their table order. Request and presentation bounds do not appear in `reached_limits`: reaching a requested page size is normal pagination, an over-limit request is invalid, and display truncation has its own flag.

Active processing excludes time spent waiting for the model. Pagination and repeated reads remain inside the same cumulative limits. The producer reserves 4 KiB of each response and 64 KiB of the session response budget for fixed issue fields and valid closing structure. Ordinary payload construction stops before those reserves. Operation 128 performs no artifact access and returns a `partial` terminal response naming `max_browser_operations`; later requests are rejected. The parent likewise produces a small fixed `partial` response from reserved space if active processing or response production reaches a resource ceiling.

Artifact-derived display strings escape C0 controls, DEL, bidirectional controls, newlines outside line framing, and Rich markup as visible ASCII. Truncation is explicit. Search and read accept only strict UTF-8 text; undecodable data is skipped with a fixed issue code rather than replacement-decoded.

## `list_members`

`list_members` pages through the existing inventory in target order. It does not rediscover containers or reread member bodies.

```json
{
  "browser_version": "pilot-v1",
  "artifact_sha256": "<64 lowercase hex characters>",
  "operation": "list_members",
  "cursor": null,
  "limit": 200
}
```

The request has exactly the five fields shown. `cursor` is null or a valid list cursor, and `limit` is an integer from 1 through 200.

The response adds exactly `targets`, `next_cursor`, and `inventory_complete`. A direct input has one target with `target_id: "artifact"` and `member_id: null`. ZIP targets use `target_id` equal to their `member_id`.

Each target contains exactly `target_id`, nullable `member_id`, nullable `container_id`, nullable `parent_member_id`, `name_display`, `name_truncated`, nullable `duplicate_ordinal`, `kind`, nullable `compressed_size`, nullable `expanded_size`, `issue_codes`, `readable`, and `searchable`. IDs, kinds, sizes, and issue codes copy the checker inventory. A direct-file target has null container, parent, and duplicate fields; a ZIP member's duplicate ordinal is one-based. A listing page with `next_cursor` may have `status: complete`, but it cannot establish that the inventory contains no later target. `inventory_complete` is true only on the final page and only when checker inventory itself completed.

For a direct input, `readable` and `searchable` are true only for the four supported direct source kinds. For a ZIP target, both flags are true only when `kind` is `file`, structural metadata does not identify encryption or unsupported compression, and the case-insensitive name does not end in `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.gz`, `.pdf`, or `.docx`. Direct unsupported inputs and those named member formats use `unsupported_format`; directories, ZIP holder members, links, special entries, encrypted entries, and unsupported compression use their corresponding skip reason. Other regular members are eligible regardless of extension, but a later strict UTF-8 failure returns `decode_error` and no text. The flags therefore mean that a text attempt is structurally permitted, not that decoding is guaranteed.

## `search_members`

`search_members` performs a case-sensitive literal Unicode search over strict UTF-8 text. It performs no regular-expression, glob, normalization, source parsing, or locale-dependent matching.

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

The request has exactly the seven fields shown. `target_id` is `all`, `artifact`, or one exact `member:N` identity; `query` contains 1 through 120 Unicode code points; `cursor` is null or a valid search cursor; and `limit` is an integer from 1 through 100.

The response adds exactly `matches`, `next_cursor`, `search_complete`, `searched_target_count`, `skipped_targets`, and `searched_bytes`. Results follow target discovery order and then byte offset. Each match contains exactly `target_id`, zero-based `byte_offset`, one-based `line`, escaped `snippet_display`, and `snippet_truncated`. Each skipped-target object contains exactly `target_id` and `reason_code`. Counts are nonnegative integers.

Only an exhaustive response sequence with `search_complete: true`, no skipped target, and no reached limit can support a claim that this exact literal was not found within the supported text boundary. It cannot support a broader absence claim.

## `read_member`

`read_member` returns one bounded, line-numbered UTF-8 window selected by stable identity, never by member name.

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

The request has exactly the six fields shown. `target_id` is `artifact` for a supported direct input or one exact readable `member:N`; `start_line` is a positive integer; and `max_lines` is an integer from 1 through 200.

The response adds exactly `start_line`, nullable `end_line`, escaped `text`, nullable `next_line`, `end_of_text`, `text_truncated`, and `returned_bytes`. `returned_bytes` counts decoded UTF-8 bytes before JSON escaping and cannot exceed 64 KiB. An overlong final line is cut at the byte bound, sets `text_truncated`, and sets `next_line` to the following line, so the omitted tail is not browsable through `read_member`. `next_line` is null only when the returned window reaches the decoded text's end. A partial, failed, or truncated read cannot support an absence claim.

## Version transition

Both protocol pilots use this contract. After both pilots, one reviewed successor may change operation details or fixed browser limits before the 25-artifact survey. The successor receives a new version and is used unchanged for every survey artifact.

A required safety change during the survey stops the run for a plan-owner decision. Responses and cursors are not coerced between versions, and a browser failure is recorded as a technical error rather than repaired by granting broader access.

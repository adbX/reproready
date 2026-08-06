# CLI

ReproReady ships one command, `reproready score`, which grades one or more code
artifacts and prints a readiness report.

## `reproready score`

```
reproready score PATH... [--json] [--validate] [--model MODEL]
```

Each `PATH` is a **directory**, a **`.zip`**, or a **`.tar.gz`**/`.tgz`. The
archive kind is inferred from the filename; a directory is walked in place.
Multiple paths score independently and print in sequence.

| Flag | Effect |
|---|---|
| `--json` | Emit a JSON document instead of the rendered report. A single path emits one object; multiple paths emit an array. |
| `--validate` | Run the promote-only Validation model call (see below). |
| `--model MODEL` | Model id for `--validate` (defaults to the built-in Validation model). |

A path that does not exist exits with status `2`.

## The rendered report

```
╭───────────────────────────── ReproReady ──────────────────────────────╮
│ examples/demo-artifact  kind=dir  has_code=True                        │
│ scope E/I/X/V = 1/1/1/1  runnable=5  readme=README.md                  │
│ R=0.93  tier=3  coverage=4/4                                           │
│ stage R  E=1.00  I=1.00  X=1.00  V=0.75                                │
╰────────────────────────────────────────────────────────────────────────╯
```

- **scope E/I/X/V** — which of the four stages are in scope for this artifact
  (a stage the artifact cannot be expected to answer is dropped, not scored 0).
- **R** — the ReproReady score over the in-scope stages; **tier** and
  **coverage** summarise it.
- **stage R** — the per-stage score `R_s`, the geometric mean of a stage's two
  channels.

Below the panel, a table lists every graded cell — its stage, channel, in-scope
flag, grade, the detector id that fired, and the routed file pointers.

## JSON output

`--json` emits the same information as a machine-readable document: the scope
map, the per-stage `vector`, `r`, `tier`, `coverage`, the flat `grid` (one entry
per cell, with `detector_id`, `pointers`, and `evidence`), and an `evidence`
summary per read file. This is the form to pipe into downstream tooling.

## The Validation call

`--validate` runs a single **promote-only** model call that can lift the
`V/implementation` cell from its deterministic floor (`0.5 → 1.0`) when the code
demonstrably reconstructs the reported results. It never lowers any grade, and
it touches only that one cell; every other grade stays deterministic.

It requires the `llm` extra and an API key:

```sh
uv add 'reproready[llm]'
export ANTHROPIC_API_KEY=...
reproready score my-project/ --validate
```

Without `--validate`, scoring is fully deterministic and offline.

# ReproReady — implementation reference

The **code-faithful** companion to the [spec](spec.md): every threshold,
vocabulary, version stamp, and table the scorer actually uses, kept in lock-step
with `src/reproready/`. The spec says *why* each rule exists; this page says
*exactly what the rule is*. When the two disagree, the code wins and this page is
the bug — bump the relevant `*_VERSION` and fix it here in the same change.

No code is reproduced below — only the **decisions** baked into it (the
constants, the rule order, the caps). For a runnable quickstart see the project
README.

## Layers

The scorer is three cheaply-separable layers plus a contained phase-2 call, each
a pure stage over the previous stage's in-memory output. Only L1 reads the
artifact's bytes; there is no store and no database — `score.py` runs the whole
pipeline in memory and returns an `ArtifactReport`.

| Layer | Module(s) | Reads | Produces | Byte access? |
|---|---|---|---|---|
| **L1 — inventory + evidence** | `inventory.py`, `content.py`, `extract.py` | archive / directory bytes (once) | the file inventory + line-numbered evidence | reads bytes |
| **L2 — rubric** | `routing.py`, `scope.py`, `rubric.py` | inventory + evidence | the 4 × 2 graded cell grid | offline |
| **L3 — aggregate** | `aggregate.py` | the grid | `R`, stage vector, tier, coverage, redundancy | offline |
| **phase-2 — validate** | `prompts.py`, `validation.py` | the grid + evidence + one model call | updated `V/implementation` grade, re-aggregated report | one model call per candidate |

The split keeps each concern independent: the deterministic rubric (L2) and
aggregation (L3) never re-open the artifact — they run over the evidence L1
already extracted, so a rubric or aggregation change re-grades from that evidence
with no new byte read. `score.py` orchestrates the layers; `cli.py` (the
`reproready score` console script) is the entry point.

## Version stamps

Each stamp names the version of one deterministic layer. The package re-exports
the five `*_VERSION` values (`from reproready import ROUTING_VERSION, …`) so a
caller can record which logic produced a report; bumping a stamp marks that the
matching layer's output changed and any stored report is stale. They are version
identifiers, not a frozen specification — the metric is still evolving.

| Stamp | Value | Lives in | Governs |
|---|---|---|---|
| `ROUTING_VERSION` | `routing-v2` | `routing.py` | the basename → `(stage, channel)` routing table |
| `RUBRIC_VERSION` | `rubric-v1` | `rubric.py` | the deterministic cell grading |
| `EXTRACT_VERSION` | `extract-v1` | `extract.py` | evidence extraction (the only layer that reads the artifact's bytes) |
| `PROMPT_VERSION` | `validate-v1` | `prompts.py` | the phase-2 Validation prompt |
| `VALIDATION_MODEL` | `claude-haiku-4-5-20251001` | `prompts.py` | the model the Validation call uses |
| `TIER_SCOPE_VERSION` | `tier-scope-v1` | `aggregate.py` | the tier-cut + scope labelling (`PROMOTED_CONFIG`); `R` unchanged |

`TIER_SCOPE_VERSION` freezes only the tier-cut and scope **labelling** — the `R`
*formula* is the spec default and is deliberately **not** stamped (see the spec's
[non-compensation note](spec.md#the-score)).

## Routing

`routing.py` classifies a path into **at most one** `(stage, implementation)` cell by the
**first matching rule** in `RULES`. README files are *not* routed (the
Documentation channel is handled by `scope`); junk / vendored paths return
`None`. Every non-README file that matches goes to the **Implementation** channel
of its stage.

**Rule precedence (first match wins).** Order encodes the resolution: specific
Env/Inputs/Validation/Execution *name* rules fire before the broad Execution
`CODE_EXTS` catch-all, which fires before the broad Inputs config/data catch-all.

1. **Environment** manifests — `requirements*.txt`, `environment*.ya?ml`,
   `conda*.ya?ml`, `Pipfile(.lock)`, `pyproject.toml` / `setup.{py,cfg}`,
   `Dockerfile*`, `Singularity*`, `renv.lock` / `DESCRIPTION`, any `*.lock`,
   a `.devcontainer/` dir.
2. **Environment** provisioning scripts — `(install|setup|build|env|create_env|make_env).sh`.
3. **Inputs** acquisition scripts (code ext) — `download|get_data|getdata|fetch|preprocess|prepare_data|prepare_dataset|make_dataset|build_dataset`.
4. **Validation** eval/plot scripts (code ext) — `eval|evaluate|evaluation|plot|analyze|analyse|analysis|visualize|visualise`.
5. **Execution** pipeline drivers (code ext) — `run_all|run_experiments|run_exp|reproduce|repro|run_pipeline|pipeline`; `train.py`.
6. **Validation** result producers — `table|fig|figure|plot|generate_|gen_` names; `expected*`; `*results*.(csv|json|txt|tsv)`; `*.log`; a `results|outputs|output|logs` dir.
7. **Execution** entry points — `(main|run|app|cli|demo|launch|__main__).py`; `makefile|gnumakefile|dvc.yaml|dvc.lock|snakefile`.
8. **Execution** computational catch-all — any `CODE_EXTS` file (the *weak credit*: a runnable unit, scope-in, but caps at `0.5` without an orchestrator).
9. **Inputs** declarative — `CONFIG_EXTS`; `DATA_EXTS`; a `data|datasets|dataset|config|configs` dir; `(download|data|dataset|urls?|links?)*.txt` pointers.

**Extension vocabularies.**

| Set | Used for | Members |
|---|---|---|
| `CODE_EXTS` | runnable units (X scope) + the X catch-all | `.py .ipynb .sh .r .jl .m .cpp .cc .cxx .c .cu .h .hpp .java .scala .go .rs .js .ts .lua .f .f90` |
| `CONFIG_EXTS` | Inputs configs | `.yaml .yml .json .gin` |
| `DATA_EXTS` | Inputs data (routing + the I-strong byte check) | `.csv .tsv .npy .npz .parquet .jsonl .h5 .hdf5 .pkl .pickle .mat .arrow .feather .libsvm` |
| `RENDERABLE_README_EXTS` | Documentation-channel precondition | `"" .md .rst .txt .markdown` |
| `JUNK_DIRS` | dropped before everything | `.git __pycache__ .ipynb_checkpoints node_modules __macosx .tox .mypy_cache .pytest_cache site-packages .venv venv .eggs bower_components .idea .vscode` |
| `JUNK_BASENAMES` | dropped before everything | `.ds_store thumbs.db` |

`FIGURE_EXTS` (`.png .pdf .eps .jpg .jpeg .svg`) is **declared but not wired into
`RULES`** — figures reach Validation by *name* (rule 6), not by extension. Don't
document it as an active route.

## Scope

`scope.py` decides which stages a static read scores, from the file **inventory**
alone (no bytes). `FALSE_ZERO_STAGES = {I, V}` for the A5 correction.

| Stage | In scope iff |
|---|---|
| **Environment** | always (code always needs an environment) |
| **Inputs** | always by default (the *confirmed self-contained* drop needs the parked import-scan) |
| **Execution** | `n_runnable ≥ 2` distinct `CODE_EXTS` files (junk-pruned) — a one-file artifact has nothing to orchestrate |
| **Validation** | always (the paper reports results to check) |

**README pick.** The shallowest README with a `RENDERABLE_README_EXTS` extension
(ties broken alphabetically). If a README exists only in an unparseable form
(e.g. `README.pdf`) → `readme_dark = 1`: the Documentation channel goes dark and the stage
it would have lit is treated as unobservable (docks coverage; with A5, dropped).

**`is_unobservable_zero(implementation_grade, documentation_grade, has_readme, readme_dark)`** — the shared
predicate behind both the Track-0 *measurement* and the A5 *fix*: a stage's
`R_s = 0` is a **false** (unobservable) zero iff the Implementation channel is mute
*and* the Documentation channel is unreadable (no README, or `readme_dark`). Any routed
implementation pointer, a documenting README, or a readable-but-silent README makes it a
**true** zero.

## Grading rubric

`rubric.py` grades every cell `0 / 0.5 / 1`. **Presence (`0.5`) is the
routing/README match** — the [routing](#routing) rules decide which files land a
cell at `0.5` (Environment = rules 1–2, Inputs = rules 3 + 9, Execution =
rules 5 + 7 + 8, Validation = rules 4 + 6); there is no separate "`0.5`" condition
here because presence is a routing question, settled before any bytes are read. The
checks **below are the `0.5 → 1` strength check only** (Implementation), or are
structurally forbidden (Documentation caps at `0.5`). The strong checks live in
`STRONG_CHECKS` as named, individually swappable functions — perturbing one is a
deliberate rubric change and bumps `RUBRIC_VERSION`.

**Environment `→ 1` (`_env_strong`)** — any of:

- a `*.lock` / `Pipfile.lock`;
- `==` in a `requirements*` file;
- a versioned conda/`environment*` (`name = version`);
- a Dockerfile `FROM image:tag` or `@sha256` with `:latest` **absent**;
- `==` in `pyproject.toml` / `setup.py` / `setup.cfg`.

(Note: pip `--hash` / `pkg @ url` pinning is **not** matched in v1 — only `==`.)

**Inputs `→ 1` (`_inputs_strong`)** — **all of** (the lone *all-of* strength):
data-obtainable **and** a real config. The two halves may be carried by
*different* files (e.g. data from a `download.sh`, the config from a separate
`config.yaml`).

- *data-obtainable* = any of: an acquisition-named script (rule-3 names); a
  pointer `*.txt` (download/data/dataset/urls/links) whose text contains `http`;
  total `DATA_EXTS` bytes **> 50 000**; or **≥ 3** `DATA_EXTS` files.
- *real config* = a `CONFIG_EXTS` file with length **> 64** chars, **≥ 2**
  non-comment / non-blank body lines, and a `:` or `=`.

**Execution `→ 1` (`_exec_strong`)** — any of:

- an orchestrator file: `makefile|gnumakefile|dvc.yaml|dvc.lock|snakefile`;
- a pipeline-driver-named code file (rule-5 names);
- an entry point (`main|run|app|cli|demo|launch|__main__|train`.py) whose body
  matches `subprocess.|os.system|check_call|check_output`.

**Validation** — **no** deterministic strong check; held at the `0.5` floor by
L2. Only the phase-2 model call ([aggregation and validation](#aggregation-and-validation))
promotes it to `1`.

**How the signals combine.** `_env_strong` and `_exec_strong` each scan every
routed file and return on the first qualifying signal, so one match anywhere in the
archive promotes the cell and the manifests need not agree. `_inputs_strong` is the
only check that requires two conditions at once — `data_obt and cfg_real` — and
because each half scans the file list independently, the two halves may be carried
by *different* files. Presence (`0.5`) is likewise satisfied by a single routed
file of the cell's type. Validation is the only cell with no enumerated test; its
promotion is the model call below.

**What each strength check reads.** A deterministic strong check is not always a
content read. Most `0.5 → 1` promotions open the file, but the strongest single
artifacts short-circuit on **presence alone**: `_env_strong`'s lockfile branch and
`_exec_strong`'s orchestrator-file and pipeline-name branches return before reading
bytes. `_inputs_strong` always reads a config (`cfg_real` is mandatory; its
data-obtainable half mixes name/size/count *inventory metadata* with one
pointer-URL content read). Validation's strength is the only purely semantic one,
and the only Implementation strength with no presence path. Presence itself is
structural for Implementation but a **content scan** for Documentation:
`_readme_documents_stage` must find the stage's keywords in the README, so the file
merely existing is not enough.

**Documentation (`_readme_documents_stage`)** — caps at `0.5`. A stage is *documented*
when a Markdown `#` header matching its keyword set has a non-empty body; with no
headers (`.rst`/`.txt`), a keyword anywhere in the prose; a keyword in the intro
(before the first header) also counts. Keyword sets (`STAGE_README_KEYWORDS`,
shared with the V prompt's README slice):

| Stage | Keywords (case-insensitive substrings) |
|---|---|
| **E** | install, setup, requirement, depend, environment, conda, pip, docker, build, prerequisite |
| **I** | data, dataset, download, input, preprocess, config, hyperparam, prepare |
| **X** | usage, run, train, execut, quickstart, command, reproduce, how to, getting started, demo |
| **V** | result, evaluat, figure, table, metric, benchmark, accuracy, performance, score, report |

## Evidence extraction

`extract.py` (L1) turns each routed file's bytes into compact, **line-numbered**,
citable evidence, stamped `EXTRACT_VERSION`. Truncation always preserves *true* line
numbers and never silently drops the middle (gaps marked `… N lines elided …`);
tokens are a zero-dependency `chars / 4` estimate.

**Caps.** Per-file budget `PER_FILE_TOKEN_CAP = 1500`; the README gets
`README_TOKEN_CAP = 4000` (it carries all four Documentation cells). `score.py` reads at
most `EVIDENCE_FILES_PER_CELL = 12` implementation pointers per stage, plus the README.

**Per-kind extraction.**

| kind | files | extraction |
|---|---|---|
| `manifest` / `shell` / `readme` / `other` | requirements/locks/Dockerfile/yaml, `*.sh`/Makefile, `README*`, other code | verbatim; head (~70 %) + tail (~30 %) if over cap |
| `python` | `*.py` | verbatim ≤ cap; else AST skeleton; `SyntaxError` → head+tail |
| `notebook` | `*.ipynb` | drop outputs; markdown ~30 % budget + code AST-compressed ~70 % |
| `data_stat` | `DATA_EXTS` + data dirs | path + size only; a header peek (first **8** lines) for `.csv/.tsv/.json/.jsonl` **≤ 64 KB** (`DATA_HEADER_MAX_BYTES`) |

Large data files are **not** read — existence is the signal (`score.py` skips
pulling their bytes).

**Python AST skeleton** keeps: the module docstring, imports, top-level config
assigns (first 3 lines), every `def`/`class` header + first docstring line, and
every **whitelisted call-site**. The whitelist `_CALL_PATTERNS` (substring of the
dotted call name) is the machine-actionable signal per stage:

- argparse — `argumentparser`, `add_argument`, `parse_args`
- output writers — `plt.`, `pyplot`, `savefig`, `sns.`, `imsave`, `.to_csv`, `np.save`/`savez`/`savetxt`, `numpy.save`, `json.dump`, `write_csv`
- checkpoints — `torch.save`/`load`, `load_state_dict`, `save_pretrained`
- shelling out — `subprocess.`, `os.system`, `check_call`, `check_output`
- data acquisition — `load_dataset`, `urlretrieve`, `urllib`, `requests.get`, `wget`, `download`, `from_pretrained`, `hf_hub_download`, `load_data`
- plus a write-mode `open(..., 'w'/'a'/'x')`

## Aggregation and validation

### `AggregationConfig` (`aggregate.py`)

A pure function over the graded cells, parameterised so every aggregation
variant is *the same cells, a different config*. Defaults reproduce the spec
exactly.

| Field | Default | Knob |
|---|---|---|
| `within_stage` | `noisy_or` | the parallel-channel rule |
| `across_stage` | `geomean` | `geomean` \| `min` \| `arithmetic` |
| `stage_weights` | `EQUAL` (0.25 each) | `TILTED` = E/I 0.15, X/V 0.35 |
| `documentation_cap` | `0.5` | the Documentation ceiling |
| `leave_out` | `None` | leave-one-stage-out arm |
| `tier_scheme` | `baseline` | `recut` (A4) re-bands `[0.5, 1]` |
| `tier_cuts` | `(0.66, 0.80)` | `TIER_RECUT_CUTS` |
| `false_zero_scope` | `False` | `True` (A5) drops unobservable I/V zeros |
| `zero_floor`, `missing_stage_value` | `0.0`, `0.0` | D1/D2 floor-softening **diagnostics** — compensatory, never promoted |

**`PROMOTED_CONFIG = recut + false_zero_scope`** (stamped `TIER_SCOPE_VERSION`) is
the canonical scorer. `score_path` applies it by default, so a plain `reproready
score` re-bands the tiers (A4) and scopes out false zeros (A5). The re-cut is a
**pure relabel** — `R` is identical either way; the scope correction changes only
which stages the geometric mean counts (unobservable ≠ absent).

The remaining knobs are **diagnostics**: `across_stage = min | arithmetic`,
`zero_floor`, and `missing_stage_value` each soften the metric's non-compensation
— a single zeroed stage no longer floors `R`. They exist so that behaviour can be
*measured*, but they are compensatory and so contradict the readiness construct;
they are never the default and never shipped.

### The phase-2 Validation call (`validation.py`, `prompts.py`)

- **Candidate** = `V` in scope **and** a non-empty `V/implementation` pointer list
  (result-producers). No producers → `V/implementation = 0` deterministically and
  **no call** is made.
- **Manuscript-free context.** The call sees no paper text — only each producer's
  basename plus the output-writing call-sites extracted from it, and the README's
  results/evaluation slice. Caps: `PRODUCERS_CAP = 12`, `SNIPPET_MAX_CHARS = 1000`,
  `README_SLICE_MAX_CHARS = 1500`. `temperature = 0` with a fixed system prompt and
  three few-shots makes the verdict reproducible.
- **Promote-only verdict.** `promote=true` → `V/implementation` grade `→ 1.0`, detector
  `V/implementation/model/promoted`, evidence `{result_ref, confidence}`. `promote=false`
  / malformed / `None` → grade **stays 0.5**, detector `V/implementation/model/kept`
  (the `detector_id` distinguishes "called, kept" from the deterministic floor
  "present"). The report then re-aggregates under `PROMOTED_CONFIG`.
- **The call.** `validate_report` builds the context, makes one
  `client.messages.create` call, and applies the verdict. `anthropic` is imported
  lazily (the `llm` extra) and only when no client is supplied; the model defaults
  to `claude-haiku-4-5-20251001` (override with `--model`) and needs
  `ANTHROPIC_API_KEY`. Driven by `reproready score --validate`.

## CLI

The tool ships one console script, `reproready`, with a single subcommand:

```
reproready score PATH... [--json] [--validate] [--model MODEL]
```

`score` takes one or more artifact paths — each a directory, a `.zip`, or a
`.tar.gz` — runs the full in-memory pipeline on each, and prints a report.

| Flag | Effect |
|---|---|
| *(none)* | Render a rich report per artifact: a summary panel (`R`, tier, coverage, per-stage `R_s`, scope, README) and a table of the 4 × 2 cells (grade, detector, pointers). |
| `--json` | Emit the report(s) as JSON instead — the full grid, pointers, an evidence summary, the stage vector, `R`, tier, coverage, redundancy. One object for a single path, a list for several. |
| `--validate` | After scoring, run the promote-only [Validation call](#aggregation-and-validation). Needs `ANTHROPIC_API_KEY` and the `llm` extra (`pip install 'reproready[llm]'`). |
| `--model MODEL` | Model for `--validate` (default `claude-haiku-4-5-20251001`). |

A missing path exits with status 2.

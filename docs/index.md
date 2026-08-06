# ReproReady

**A static reproduction-readiness score for a code artifact: how well it equips
an independent researcher to regenerate the reported results — judged without
running any code.**

ReproReady measures **readiness, not executability**. It does not predict
whether the code would ultimately run; it measures whether the artifact removes
the manual effort a reproduction would otherwise cost. Point it at a directory,
a `.zip`, or a `.tar.gz` and it returns a score `R ∈ [0, 1]`, a tier, and a
graded grid you can inspect cell by cell.

## The model in one screen

Reproduction is modelled as four stages, each judged on two independent
channels — the **implementation** (machine-actionable files) and the
**documentation** (prose that names and explains them):

| Stage | What the artifact must answer |
|---|---|
| **E** — Environment | What does the code need to run? |
| **I** — Data / Inputs | Where does the data come from and how is it prepared? |
| **X** — Execution | How is the reported result actually produced? |
| **V** — Validation | How do the outputs map back to the paper's claims? |

The score is **non-compensatory**: a stage the artifact leaves unanswered
cannot be bought back by another. See the [specification](spec.md) for the full
model, grading, tiers, and worked examples.

## Get started

```sh
uv add reproready            # or: pip install reproready
reproready score path/to/artifact.zip
```

```python
from reproready import score_path

report = score_path("path/to/artifact.zip")
print(report.r, report.tier, report.coverage)
```

## Where to go next

- **[Specification](spec.md)** — what the score measures, the grading model,
  the scope rule, tiers, and worked examples.
- **[Implementation reference](implementation.md)** — the code-faithful
  companion: layers, version stamps, and each module's behaviour.
- **[CLI](cli.md)** — the `reproready score` command and its output.

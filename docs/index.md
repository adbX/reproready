# Quick start

ReproReady inspects one regular file and reports exact static observations, inspection limits, and questions for a person to review. It does not run artifact code or predict whether the artifact will execute successfully.

Version `0.2.0` requires Python 3.11 or newer on macOS or Linux. Install the released checker with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install reproready==0.2.0
```

## Check a file

Save this synthetic example as `checker-demo.py`:

```python
from pathlib import Path
from sys import path

# Download https://example.invalid/research-data.zip before analysis.
DATA_PATH = Path("/example/data.csv")
path.append(".../vendor")

if __name__ == "__main__":
    print(DATA_PATH)
```

Inspect it without executing it:

```sh
reproready check checker-demo.py
```

The report records one absolute-path finding and asks a person to review the search-path and download-comment evidence. A finding or review question is not a pass/fail decision.

## Save and reopen the report

Save the complete machine-readable report, then validate and browse it without reopening the source file:

```sh
reproready check checker-demo.py --json > checker-demo-report.json
reproready view checker-demo-report.json
```

Read **Limits reached**, **Checks skipped**, and **Checks failed** before interpreting findings. Continue with the [Guide](checker.md#interpreting-output) for report navigation, supported inputs, process outcomes, and safety boundaries.

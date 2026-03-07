# TestMind

A CLI tool and Python library for ingesting test reports, detecting patterns
(flaky tests, regressions, spikes), and predicting failures based on historical
execution data.

Supports **JUnit XML** today, with the parser interface open to CSV, HTML, and
other formats.

---

## Installation

```bash
# Clone and install in editable mode
git clone <repo>
cd testmind
uv sync          # installs all dependencies + the testmind CLI
```

The `testmind` command is registered as a script entry point and is available
immediately after installation.

```bash
testmind --help
```

---

## Quick start

```bash
# First time: bulk-load historical reports to get meaningful analysis immediately
testmind ingest reports/history/*.xml --project my-service

# Day-to-day: ingest the latest run
testmind ingest reports/junit.xml --project my-service

# Check which projects you are tracking
testmind projects

# Re-run analysis on the latest stored run
testmind analyze my-service

# Browse the run history
testmind history my-service
```

By default the database lives at `~/.testmind/testmind.db`.
Override it with `--db <path>` or the `TESTMIND_DB` environment variable.

---

## Commands

### `ingest` — parse, store, analyse

```
testmind ingest <FILE> [FILE ...] --project <NAME> [OPTIONS]
```

Accepts **one or more** JUnit XML files. Each file is parsed, stored, and
counted. After all files are processed a single analysis summary is printed,
covering the full available history.

This makes it possible to bootstrap a project on the first run by pointing at
an archive of historical reports — patterns like flaky tests or regressions are
only detectable once enough history exists, so bulk-loading is the recommended
first step.

Each file is processed independently: a parse error on one file prints a
warning and moves on; the command only exits with code 1 if **every** file
fails. Duplicate reports (same content hash) are silently skipped, so running
the same command twice is always safe.

| Option | Default | Description |
|---|---|---|
| `--project / -p` | required | Project name to track the run under |
| `--format / -f` | `text` | Output format: `text` or `json` |
| `--db` | `~/.testmind/testmind.db` | SQLite database file |
| `--limit / -n` | `30` | Max historical reports loaded for analysis |

```bash
# First run: load a full archive to seed history
testmind ingest reports/history/*.xml --project payments-service

# Day-to-day: ingest the latest CI run
testmind ingest build/reports/TEST-suite.xml --project payments-service

# JSON output — useful in CI pipelines
testmind ingest reports/junit.xml --project auth-service --format json

# Project-scoped database
testmind ingest reports/*.xml --project orders --db ./data/orders.db

# Override DB via env var
TESTMIND_DB=./ci.db testmind ingest reports/junit.xml --project api
```

**Example output for a bulk ingest:**

```
Ingesting 5 reports for project 'payments-service'...
  [1/5] TEST-2024-01-01.xml           stored 'nightly-2024-01-01'  [87✓  3✗  2⊘  0!]
  [2/5] TEST-2024-01-02.xml           stored 'nightly-2024-01-02'  [90✓  0✗  2⊘  0!]
  [3/5] TEST-2024-01-03.xml           stored 'nightly-2024-01-03'  [88✓  2✗  2⊘  0!]
  [4/5] TEST-2024-01-04.xml           stored 'nightly-2024-01-04'  [91✓  0✗  1⊘  0!]
  [5/5] TEST-2024-01-05.xml           stored 'nightly-2024-01-05'  [85✓  5✗  2⊘  0!]

5 stored.

TestMind Report — project: payments-service
Run: nightly-2024-01-05  |  2024-01-05 10:00:00 UTC  |  Duration: 12.34s
...
```

---

### `analyze` — re-run analysis on the latest run

```
testmind analyze <PROJECT> [OPTIONS]
```

Runs the full analysis pipeline against the most recent stored run without
re-parsing anything. Useful when you want to re-inspect results after changing
thresholds or after more history has accumulated.

| Option | Default | Description |
|---|---|---|
| `--format / -f` | `text` | `text` or `json` |
| `--db` | `~/.testmind/testmind.db` | SQLite database file |
| `--limit / -n` | `30` | Max historical reports loaded |

```bash
testmind analyze payments-service
testmind analyze payments-service --format json | jq '.flaky'
```

---

### `projects` — list tracked projects

```
testmind projects [--db <path>]
```

Prints a table of all projects with their run count and the timestamp of the
most recent run.

```
Project                                  Reports  Latest run
----------------------------------------------------------------------
auth-service                                  12  2024-06-15 09:45
orders-service                                 8  2024-06-14 22:10
payments-service                              31  2024-06-15 10:00
```

---

### `history` — browse run history

```
testmind history <PROJECT> [--limit N] [--db <path>]
```

Prints a chronological table (newest first) of all stored runs for a project.

```
History for 'payments-service'  (showing 5 run(s))

Run                                  Timestamp               Pass   Fail   Skip    Err   Duration
--------------------------------------------------------------------------------------------------
nightly-2024-06-15                   2024-06-15 10:00:00       87      3      2      0     12.34s
nightly-2024-06-14                   2024-06-14 10:00:01       90      0      2      0     11.90s
nightly-2024-06-13                   2024-06-13 10:00:00       88      2      2      0     12.01s
```

```bash
testmind history payments-service --limit 5
testmind history payments-service --limit 100 --db ./archive.db
```

---

## Output formats

### Text (default)

The text report is structured in sections, printed only when there is
something to show.

```
TestMind Report — project: payments-service
Run: nightly-2024-06-15  |  2024-06-15 10:00:00 UTC  |  Duration: 12.34s
────────────────────────────────────────────────────────────
OVERVIEW
  Total: 92   Passed: 87   Failed: 3   Skipped: 2   Errors: 0
  Pass rate: 94.6%   Fail rate: 3.3%

FLAKY TESTS  (2)
  test_process_refund                               flip=70.0%  fail=40.0%  runs=10
  test_currency_conversion                          flip=60.0%  fail=30.0%  runs=10

REGRESSIONS  (1)
  test_checkout_timeout                             ref_pass=100.0%  recent_fail=66.7%

STABILITY INDEX  (worst 10 of 87 tests)
  Test                                               Score  Pass    Consist  Flips
  test_process_refund                                 38.0  60.0%  95.0%   70.0%
  test_currency_conversion                            44.0  70.0%  92.0%   60.0%
  ...

FAILURE PREDICTIONS  (top 10 by risk)
  Test                                               Prob    Trend       Confidence
  test_checkout_timeout                              78.0%  degrading   55.0%
  test_process_refund                                45.0%  stable      50.0%
  ...

ISSUES: 2 flaky  |  1 regression(s)  |  0 spike(s)
```

A **spike banner** is injected at the top when a sudden suite-wide failure
surge is detected:

```
  FAILURE SPIKE DETECTED
  Current fail rate : 48.0%
  Baseline          : 3.2% ± 1.1%
  Z-score           : 40.73
```

### JSON

Pass `--format json` to get a machine-readable object. Useful for piping into
`jq`, posting to Slack, or feeding downstream tools.

```json
{
  "project": "payments-service",
  "report": {
    "id": "a3f9c...",
    "name": "nightly-2024-06-15",
    "timestamp": "2024-06-15T10:00:00+00:00",
    "duration": 12.34,
    "passed": 87,
    "failed": 3,
    "skipped": 2,
    "errors": 0,
    "total": 92,
    "pass_rate": 0.9457,
    "fail_rate": 0.0326
  },
  "issues": {
    "flaky_count": 2,
    "regression_count": 1,
    "spike_detected": false
  },
  "flaky": [
    {
      "test_name": "test_process_refund",
      "is_flaky": true,
      "flip_rate": 0.7,
      "pass_rate": 0.6,
      "fail_rate": 0.4,
      "run_count": 10,
      "insufficient_data": false
    }
  ],
  "regressions": [ ... ],
  "spike": null,
  "stability": [ ... ],
  "predictions": [ ... ]
}
```

```bash
# Extract only flaky tests from a CI run
testmind ingest reports/junit.xml --project api --format json \
  | tail -n +2 \
  | jq '[.flaky[] | {test: .test_name, flip_rate: .flip_rate}]'

# Fail CI if regressions are detected
COUNT=$(testmind analyze my-service --format json | jq '.issues.regression_count')
[ "$COUNT" -gt 0 ] && exit 1
```

---

## Python library usage

Every component is importable and composable independently.

### Parse a report

```python
from testmind.parsers.junit_parser import JUnitParser

parser = JUnitParser()
report = parser.parse("reports/junit.xml", project="my-service")

print(report.name, report.pass_rate, report.fail_rate)
for test in report.tests:
    print(test.name, test.status, test.duration)
```

### Store and retrieve history

```python
from testmind.storage.sqlite_store import SQLiteStore

store = SQLiteStore("~/.testmind/my-service.db")
store.save_report(report)

# All runs for a project, newest first
reports = store.get_reports("my-service", limit=20)

# Per-test history across runs: list[(datetime, TestResult)]
history = store.get_test_history("my-service", "test_checkout", limit=30)

store.close()
```

### Run individual analysers

```python
from testmind.analysis.flaky import FlakyDetector
from testmind.analysis.regression import RegressionDetector, SpikeDetector
from testmind.analysis.stability import StabilityAnalyzer
from testmind.analysis.predictor import FailurePredictor

history = store.get_test_history("my-service", "test_checkout", limit=30)

flaky   = FlakyDetector().analyze("test_checkout", history)
regr    = RegressionDetector().analyze("test_checkout", history)
stable  = StabilityAnalyzer().analyze("test_checkout", history)
pred    = FailurePredictor().analyze("test_checkout", history)

print(flaky.is_flaky, flaky.flip_rate)
print(regr.is_regression)
print(stable.score)         # 0–100
print(pred.failure_probability, pred.trend)
```

### Generate a full summary

```python
from testmind.reports.summary import Summarizer
from testmind.reports.formatters import TextFormatter, JsonFormatter

# report must already be saved in the store
summarizer = Summarizer(history_limit=30)
summary = summarizer.summarize("my-service", store)

print(TextFormatter().format(summary))
print(JsonFormatter().format(summary))
```

---

## Under the hood

### Storage

All data is persisted in a **SQLite database** (stdlib `sqlite3`, no ORM).
Two tables:

- `reports` — one row per ingested run (name, project, timestamp, pass/fail/skip/error counts, duration)
- `test_results` — one row per test case, linked to its report

Reports are deduplicated by a **SHA-256 content hash** derived from project
name, duration, timestamp, and test count. Ingesting the same file twice is
always safe.

### Pattern detection

All analysers operate on the per-test history: a list of
`(timestamp, TestResult)` pairs retrieved from the store. They require a
minimum number of runs before drawing conclusions (`insufficient_data=True`
is returned otherwise).

#### Flaky test

A test is flaky when it produces **mixed results** without a clear directional
trend.

```
is_flaky = fail_rate ∈ (0.10, 0.90)   # not consistently passing or failing
         AND flip_rate > 0.15          # consecutive outcomes differ often

flip_rate = |{consecutive pairs that differ}| / (n - 1)
```

Default minimum: **5 runs**.

#### Regression

A test is a regression when it was **stable and has recently broken**.

```
reference window = all runs except the last 3
recent window    = last 3 runs

is_regression = reference_pass_rate >= 0.90   # was stable
              AND recent_fail_rate  >= 0.60   # now failing
```

Default minimum: **6 runs total**.

#### Spike

A spike is a sudden **suite-wide** increase in failure rate in the latest run
compared to the rolling baseline.

```
baseline        = fail_rate of all previous runs in the window
z_score         = (current_fail_rate - baseline_mean) / baseline_std

is_spike        = z_score >= 2.0 AND current_fail_rate > baseline_mean
```

Requires at least **3 baseline reports**.

#### Stability index (0 – 100)

A composite score per test:

```
score = pass_rate            × 60
      + duration_consistency × 20
      + (1 − flip_rate)      × 20

duration_consistency = 1 − min(CV, 1)
  where CV = std(durations) / mean(durations)
```

A perfectly stable test (always passes, consistent timing, never flips) scores
**100**. A consistently failing test with stable timing scores **40**. A
maximally flaky test scores near **0**.

#### Failure prediction

A lightweight trend model — no external dependencies, no ML framework.

```
1. Encode each run as 1.0 (fail/error) or 0.0 (pass/skip).
2. Fit an OLS linear regression on the sequence (index → outcome).
3. Predict next value = mean(last 3 outcomes) + slope.
4. Clamp to [0, 1].

slope > +0.05  → DEGRADING
slope < −0.05  → IMPROVING
otherwise      → STABLE

confidence = min(run_count / 20, 1.0)
```

### Architecture

```
src/testmind/
├── domain/
│   └── models.py          TestResult, TestReport, TestStatus
├── parsers/
│   ├── base.py            Abstract ReportParser
│   └── junit_parser.py    JUnit XML parser
├── storage/
│   ├── base.py            Abstract Store
│   └── sqlite_store.py    SQLite implementation
├── analysis/
│   ├── models.py          Result dataclasses + Trend enum
│   ├── flaky.py           FlakyDetector
│   ├── regression.py      RegressionDetector, SpikeDetector
│   ├── stability.py       StabilityAnalyzer
│   └── predictor.py       FailurePredictor
├── reports/
│   ├── summary.py         RunSummary, Summarizer
│   └── formatters.py      TextFormatter, JsonFormatter
└── cli/
    └── app.py             Typer CLI (ingest, analyze, projects, history)
```

---

## Running tests

```bash
uv run pytest              # all 173 tests
uv run pytest tests/parsers/
uv run pytest tests/analysis/
uv run pytest tests/storage/
uv run pytest tests/reports/
uv run pytest tests/cli/
uv run pytest --cov=src/testmind --cov-report=term-missing
```

---

## Configuration reference

| Env var | CLI flag | Default | Description |
|---|---|---|---|
| `TESTMIND_DB` | `--db` | `~/.testmind/testmind.db` | Path to the SQLite database |
| — | `--format` | `text` | Output format for `ingest` and `analyze` |
| — | `--limit` | `30` | Max historical reports loaded per analysis |

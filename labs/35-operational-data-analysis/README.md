# Lab 35 Operational Data Analysis

This lab compares two releases using recorded task attempts. It checks
the input rows, calculates duration and failure summaries, and displays the
results on a local web page. The supplied CSV is synthetic but has the fields
an operational export would need.

The report asks whether the releases differ and how queue depth and region
relate to duration. It includes uncertainty and sample limitations rather
than treating one average as a release decision. It describes the sample;
it does not predict future tasks.

## Goal and working order

Compare releases using validated observations without predicting the next
job's duration. Start with supplied analysis code and a
synthetic fixture, then inspect rejected rows, compare releases and test one
schema change. The dataset is not a measurement of SigRaft in production.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
NumPy, pandas, SciPy, Matplotlib and statsmodels are actual runtime dependencies,
declared in `pyproject.toml`; the HTTP page uses the standard library.
No telemetry backend or Azure subscription is needed.

1. Install and run the REPL loader before opening the web page.
2. Observe 329 valid rows, six rejected rows and nine repeated task IDs in the
   provided fixture. These are attempts, not 329 independent jobs.
3. Run `pytest -q tests/test_schema.py tests/test_analysis.py tests/test_pipeline.py`.
   Explain the interval's target and the distinction between effect size and
   statistical significance.
4. Add a malformed-row test or change a copy of the CSV, then inspect rejection
   reasons. Render the page and compare the numerical report with its chart.

## Install and run the gates

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run and supplied no verdict; repair its setup before claiming a pass.

## Open the web page

```bash
sigraft-analysis
```

Then open `http://127.0.0.1:8035/` in a browser. The service binds to
`127.0.0.1` by default so it is not reachable from outside the machine
running it. `GET /healthz` reports only that the process is responding, not
that the dataset loaded or the analysis succeeded. `GET /` therefore reports
its own failures with a generic JSON error. The full exception stays in the
server log; the response contains no exception text or filesystem path.
Every response also carries `Content-Security-Policy`, `X-Content-Type-Options` and
`Referrer-Policy` headers, appropriate to a page with no script and no
external resource. Stop the server with Ctrl-C.

To point the service at a different export:

```bash
sigraft-analysis --data path/to/observations.csv --port 8080
```

## Dataset schema

`src/lab_35_operational_data_analysis/data/observations.csv` is a small,
synthetic, deterministic fixture invented for this lab. It contains no
company data and was not downloaded from anywhere. Every row is one
completed job attempt:

| Column | Type | Meaning |
|---|---|---|
| `timestamp` | UTC, `Z`-suffixed ISO 8601 | when the attempt completed |
| `task_id` | string | the task this attempt belongs to, for example `task-2024050-0001` |
| `release` | string | the relay release that served the attempt |
| `region` | string | the region that served the attempt |
| `queue_depth_at_submit` | non-negative integer | queue depth measured when the task was submitted |
| `duration_ms` | positive float | how long the attempt took, in milliseconds |
| `outcome` | `succeeded` or `failed` | the terminal outcome |
| `attempt` | integer, 1 or greater | which attempt this is for `task_id` |

The fixture covers two releases, `2024.05.0` and `2024.05.1`, across two
regions. Nine job IDs appear more than once because a client retried after
a failure. The loader counts these attempts separately rather than merging
them. They are repeated observations of the same jobs, not independent jobs.
The last six rows are deliberately invalid, one violation each
(a timestamp with no UTC offset, a negative queue depth, a missing duration,
an outcome outside the documented set, a missing task ID, a missing release),
so the loader always has something real to reject and count.

The schema is enforced beyond the column list. A header with a column the
schema does not document is rejected outright rather than accepted with the
extra column ignored. `duration_ms` is rejected if it parses as `NaN` or an
infinity, since Python's `float()` accepts both without raising and either
would silently poison a mean. `task_id`, `release` and `region` are bounded
to a safe character set (letters, digits, dots, hyphens and underscores) and
a fixed maximum length before they are ever kept, because these three
fields appear in the web report's HTML and SVG. Length and character checks
reject malformed values before rendering; the renderer still escapes accepted
values.

## Interpretation limits

Read these limits alongside the numerical report. A result can be calculated
correctly and still answer a different question from the one an operator
needs to ask.

- The confidence interval is for the mean attempt duration, not a percentile
  and not a guarantee about any single task. The calculation treats rows as
  independent; counting retries does not correct their statistical dependence.
- The release comparison uses Mann-Whitney to compare ranked durations.
  Its p-value measures how surprising the observed ranks would be under the
  equal-distribution null model, not how much the difference matters
  operationally. Repeated attempts can violate the independence assumption.
  Read the effect size and the release's latency objective as well.
- The regression coefficients describe an association within this sample,
  not a causal effect. Adding region to the same regression changes the sign
  of the queue-depth coefficient, because region here is correlated with
  both queue depth and duration; this is a real example of an omitted
  confounder, kept in the report rather than hidden behind one number.
- Durbin-Watson checks whether the residuals are still correlated with each
  other in row order. Plain OLS standard errors assume they are not.
- None of this predicts a future task's duration or outcome. That would be a
  modelling exercise outside this book's scope.
- The report page names the dataset file, never the absolute path it was
  read from on the server; an analysis failure is logged in full on the
  server and reported to the client as a generic message for the same
  reason.

## Layout

```
src/lab_35_operational_data_analysis/
  schema.py     documented CSV schema, validation, load report
  analysis.py   descriptive statistics, confidence interval, comparison, OLS
  report.py     the accessible SVG chart and the escaped HTML report
  pipeline.py   wires loading, analysis and rendering into one report
  service.py    the stdlib HTTP front end
  data/         the bundled CSV fixture
tests/          the test suite
pyproject.toml  dependencies, tool settings and gate definition
```

The analysis functions take a `Path` or a `DataFrame` and never open a
socket; `service.py` is the only module that knows about HTTP, and it never
contacts a live telemetry backend.

## Python REPL debugging session

After the editable install, inspect the analysis pipeline directly:

```pycon
>>> import lab_35_operational_data_analysis as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> path = lab.default_dataset_path()
>>> load_report = lab.load_observations(path)
>>> load_report.valid_count, load_report.rejected_count, load_report.duplicate_task_ids
(329, 6, 9)
>>> [row.reason for row in load_report.rejected]
>>> frame = lab.observations_to_frame(load_report.observations)
>>> lab.summarize_all_releases(frame)
>>> lab.overall_confidence_interval(frame)
>>> baseline, candidate = lab.chronological_releases(frame)
>>> lab.compare_releases(frame, baseline, candidate)
>>> lab.fit_duration_model(frame)
```

The loader separates invalid rows before analysis and reports repeated task
IDs instead of silently treating every attempt as a new job. Compare the
queue-depth coefficient before and after including region. Its changed sign
shows why the report cannot attribute longer duration to queue depth alone.

Step through `build_report` the same way to see the HTML and SVG it renders
before the HTTP service ever gets involved:

```pycon
>>> report = lab.build_report(path)
>>> report.summaries
>>> "<svg" in report.svg
>>> html = report.as_html()
>>> "Per-release summary" in html
```

## Contribution and completion

This standalone analysis lab informs the SigRaft job-orchestration web
service's release decisions. Lab 39 includes its own analysis runtime and
packaged fixture rather than importing this package.
Finish when you can explain validation losses, repeated observations,
uncertainty and confounding, and `pybootstrap check` exits 0.
Stop `sigraft-analysis` with Ctrl-C, remove only CSV/report copies you created,
and deactivate the environment. Installation and optional data acquisition are
separate from the deterministic local analysis.

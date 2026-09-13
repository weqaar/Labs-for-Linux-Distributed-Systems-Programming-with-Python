# Lab 35 Operational Data Analysis

This is the checkpoint that gives relay a basic operational data analysis
report: descriptive statistics, a confidence interval, a two-release
comparison and a small regression, computed with NumPy, pandas, SciPy,
Matplotlib and statsmodels, and shown on a local web page. It reads a CSV of
completed relay task attempts shaped like the request telemetry a real
deployment would export, validates every row against a documented schema,
and answers a fixed set of promotion and rollback questions with arithmetic
rather than a single average or a picture with no numbers behind it.

It does not train, score or predict anything. Machine learning is out of
scope for this book; see the chapter for why.

## Install and run the gates

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked; treat 2 as the more serious of the two.

## Open the web page

```bash
sigraft-analysis
```

Then open `http://127.0.0.1:8035/` in a browser. The service binds to
`127.0.0.1` by default so it is not reachable from outside the machine
running it. `GET /healthz` reports only that the process is responding; it
does not prove the dataset loaded or the analysis succeeded, which is why
`GET /` reports its own errors instead of hiding them behind a healthy
probe, as a generic JSON message with no exception text or filesystem path
in it. The full exception is logged server-side instead. Every response
also carries `Content-Security-Policy`, `X-Content-Type-Options` and
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
completed relay task attempt:

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
regions. A handful of tasks were retried by their client after a failure and
appear twice with the same `task_id` and a higher `attempt`; the loader
counts these rather than rejecting or silently merging them, because a
retried task is a duplicated observation of the same unit of analysis, not
a fresh one. The last six rows are deliberately invalid, one violation each
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
fields are the ones the web report renders into HTML and SVG; bounding them
at ingestion does not replace the escaping the renderer does, it means the
renderer never has to be the only thing standing between a malformed field
and the page.

## Interpretation limits

- The confidence interval is for the mean task duration, not a percentile
  and not a guarantee about any single task.
- The release comparison uses Mann-Whitney, appropriate for the right-skewed
  duration data here; its p-value says how surprising the split would be
  under no difference, not how much the difference matters operationally.
  Read the effect size and the release's latency objective for that.
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
>>> [row.reason for row in load_report.rejected]
>>> frame = lab.observations_to_frame(load_report.observations)
>>> lab.summarize_all_releases(frame)
>>> lab.overall_confidence_interval(frame)
>>> baseline, candidate = lab.chronological_releases(frame)
>>> lab.compare_releases(frame, baseline, candidate)
>>> lab.fit_duration_model(frame)
```

Step through `build_report` the same way to see the HTML and SVG it renders
before the HTTP service ever gets involved:

```pycon
>>> report = lab.build_report(path)
>>> report.summaries
>>> "<svg" in report.svg
>>> html = report.as_html()
>>> "Per-release summary" in html
```

# Lab 03 Quality Gate

This lab records whether a check passed, found a problem or could not
run. These are different outcomes: a missing checker supplies no evidence
about the code. The package converts those outcomes into exit codes and
JUnit XML that a continuous integration system can publish. The Python
import name in this lab is `relay`. That name does not mean the program
relays traffic.

## Capability added here

- classify each quality check as passed, failed or errored
- render JUnit XML with `<failure>` for findings and `<error>` when a
  tool could not produce a valid verdict
- keep the overall exit code accurate so `error` outranks `failure`
- publish JUnit results that Azure Pipelines can display and use to reject a build
- build the installed quality API's documentation, execute its examples and
  retrieve its local preview, practising checks also needed for SigRaft's docs

## Goal and working order

Learn to distinguish a check that found a defect from a tool that never checked
anything. You will inspect the Python objects that represent a check result, exercise fake process
outcomes, and add executable documentation. This is release tooling for the
SigRaft job-management web service, not a running job server or CLI.

Use Python 3.10 or later in this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`. The runtime
uses dataclasses, subprocess and XML from Python's standard library. Sphinx and
the gate tools are declared development dependencies; MkDocs is optional.
Installation may download tools, but local validation needs no subscription.

1. Install below, then inspect `quality.py` and its test fake runner.
2. Run `pytest -q tests/test_lab_03_quality_gate.py`. Trace one pass, one
   finding and one missing-tool result into `GateReport.to_junit_xml`.
3. Complete the documentation exercises and retain JUnit evidence:

```bash
pybootstrap check --junit-dir .quality
```

Azure Pipelines can then publish `.quality/*.xml` and fail closed on both
failures and errors.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

```bash
pybootstrap check
```

Direct commands stay the same locally and in CI:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```
## Python REPL debugging session

After the editable install, inspect the package actually loaded by Python:

```pycon
>>> import inspect
>>> import lab_03_quality_gate as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> [(name, type(getattr(lab, name)).__name__) for name in public]
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
>>> suite = lab.relay_quality_suite()
>>> inspect.signature(suite.run)
>>> [command.name for command in suite.commands]
['format', 'lint', 'types', 'test']
```

The list contains commands, not their results. Calling `suite.run` executes
them through the supplied runner. In the tests, that runner returns controlled
outcomes so you can inspect a finding and a missing checker separately.

## Build the documentation and execute its examples

The same distinction applies to documentation: a wrong example is a finding,
while a builder that cannot start supplies no result. The `dev` extra declares
Sphinx in `pyproject.toml`. Build the guide, execute its examples and preview
the resulting pages from this lab directory:

```bash
python -m sphinx -n -W --keep-going -b html docs build/docs/html
python -m sphinx -n -W --keep-going -b doctest docs build/docs/doctest
python -m http.server 8000 --bind 127.0.0.1 --directory build/docs/html
```

Open http://127.0.0.1:8000/ and inspect `run_gate`, the example verdict and the
custom footer. Stop the foreground preview with Ctrl-C. Serve only the HTML
output; this server provides neither production hosting nor authentication.

Autodoc imports the installed package, autosummary lists its API, napoleon
renders Google-style docstrings and doctest executes examples. HTML rendering
alone does not execute examples. The classic theme is built in;
`docs/_templates/layout.html` adds a small footer using the original template.
There is no runtime theme download or remote reference inventory.

`tests/test_documentation.py` runs both strict builders and exercises actual
failure paths in isolated copies: an unknown object reference, an incorrect
expected result and a module that raises during autodoc import. It also builds
and retrieves real HTML from a loopback server on an ephemeral port, then
shuts down and joins the server. All of this runs inside the pytest gate.

## Style and contract exercises

1. Read `docs/workflow.rst`, then inspect `run_gate` with `help` and
   `inspect.signature`. PEP 484 function hints and PEP 526 variable annotations
   describe types, not whether a missing checker is an error.
2. Expand the `GateReport.to_junit_xml` docstring to state what callers can
   retain. PEP 257 describes summary and detail structure; Google-style
   sections are an additional convention rendered by napoleon.
3. Add a deterministic missing-command example to `docs/examples.rst`.
   Assert both the report's error count and the `<error>` element. Alter an
   expected value only in a scratch copy under `build/` and inspect the failing
   doctest diagnostics.
4. Explain why `GateReport.exit_code` prioritizes errors over findings. Its
   comment records that policy, not the mechanics of an `if` statement.
   Add a comment only if another non-obvious decision needs a reason.
5. Copy `quality.py` to `build/quality-without-docstring.py` and remove one
   public docstring in the copy. Run
   `ruff check --stdin-filename src/lab_03_quality_gate/quality.py - < build/quality-without-docstring.py`
   to apply the original path's rules without editing it. Expect a missing
   docstring diagnostic, then remove the copy. The pydocstyle rules deliberately
   target this documented API, not every pre-existing test helper.

PEP 8 recommends 79-character code lines; this repository intentionally uses
100 for consistent lab formatting. `ruff format --check` enforces its configured
layout, not complete PEP 8 compliance. Pydocstyle checks selected conventions,
not the truth of a contract; examples, tests and review supply other evidence.

## Optional Markdown comparison

```bash
pip install -e ".[dev,docs-comparison]"
python -m mkdocs build --strict --config-file mkdocs.yml
python -m mkdocs serve --dev-addr 127.0.0.1:8001 --config-file mkdocs.yml
```

The optional `mkdocs/index.md` is a handwritten guide. Compare its maintenance
with Sphinx's imported API and executed examples. MkDocs core alone does not
replace those checks. Stop the preview with Ctrl-C; MkDocs is not a default gate.

## Finishing condition

`pybootstrap check` must exit 0. Its pytest gate must build HTML and doctests,
reject every seeded documentation defect and finish the loopback HTTP smoke
test. Your documentation exercise must also pass the two direct Sphinx commands.
Retain the existing JUnit tests checking pass, failure, error and error precedence.
No subscription or external service is needed for this evidence.

Explain why a missing executable becomes `<error>` rather than `<failure>`,
and why an incomplete suite cannot pass. Exit 1 means a gate found problems;
exit 2 means a gate could not run and supplied no verdict. Lab 39 retains these
release principles, not an import of this package. Stop local previews and
remove scratch documentation copies after the exercise; retain `.quality`
only as long as its evidence is useful.

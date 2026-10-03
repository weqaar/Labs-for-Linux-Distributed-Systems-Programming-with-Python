# Lab 03 Quality Gate

This checkpoint adds honest quality gate evidence for the same relay
product. It models the outcomes that protect `relayctl` and the `/tasks`
service from a lying pipeline: pass, fail and error.

## Capability added here

- classify a relay gate as passed, failed or errored
- render JUnit XML with `<failure>` for real findings and `<error>` when a
  tool never ran
- keep the overall exit code honest so `error` outranks `failure`
- publish the same evidence shape that Azure Pipelines can gate on
- gate the installed quality API's documentation, examples and local preview
  so the completed SigRaft product can retain checked reader-facing evidence

Example relay-focused flow:

```text
pybootstrap check --junit-dir .quality
relayctl submit --task-id task-17 --action rebuild-search-index
```

Azure Pipelines can then publish `.quality/*.xml` and fail closed on both
failures and errors.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
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
```

Inspect one callable signature and compare a raised application failure with a
missing checker command. The two failures must remain distinguishable.

## Documentation is executable evidence

The `dev` extra declares Sphinx in `pyproject.toml`. From this lab directory:

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
Retain the existing JUnit tests proving pass, failure, error and error precedence.
No subscription or external service is needed for this evidence.

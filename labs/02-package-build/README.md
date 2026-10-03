# Lab 02 Package Build

Build the `relayctl` Python command as standard Python distributions and as
native-looking executables for Linux and Windows. The executable contains a
Python interpreter; PyInstaller bundles the program but does not compile Python
to machine code.

The checkpoint also contains `relay-template`, a Copier template that generates
the shared `/tasks` resource and queued, running, succeeded, and failed state
contract. This avoids starting later relay services by copying a stale project.
Its Sphinx site also publishes the installed inspection API and executable
examples, establishing the documentation contract carried into SigRaft.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

The same command runs on a laptop and in CI, so a failure is always
reproducible:

```bash
pybootstrap check
```

Add `-v` to include warnings, `--gate lint` to run one gate, and `--fix` to
apply the corrections tools can make on their own.

Each gate can also be run directly, because pybootstrap does not wrap or
reconfigure them:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate ran and found problems |
| 2 | A gate could not run, so nothing was checked |

The split between 1 and 2 is the point. A missing or misconfigured tool is not
the same as clean code, and a pipeline that treats them alike will eventually
report success while checking nothing.

## Layout

```
src/lab_02_package_build/    the package
tests/                the test suite
ci/azure-pipelines.yml       Linux and Windows build jobs
relay-template/       updateable Copier template for the relay project contract
pyproject.toml        dependencies, tool settings and gate definition
```

There is no separate build description. Dependencies live where pip already
looks, tool settings live in each tool's own table, and `[tool.pybootstrap]`
adds only the list of gates.

## Build Python distributions

```bash
python -m build
python -m twine check dist/*
```

Install the wheel into a clean virtual environment and run `relayctl inspect`
there to prove the installed package, rather than the working tree, supplies
the command.

## Build an executable

Build on each target operating system. PyInstaller does not cross-compile.

```bash
python -m PyInstaller --onefile --name relayctl \
  --paths src src/lab_02_package_build/__main__.py
dist/relayctl --version
```

On Windows the output is `dist/relayctl.exe`. The pipeline builds both from the
same commit.

On Linux, inspect the file without running code from it:

```bash
file dist/relayctl
readelf --file-header dist/relayctl
readelf --program-headers dist/relayctl
readelf --dynamic dist/relayctl
dist/relayctl inspect --headers dist/relayctl
```

The file header must report ELF and the intended machine architecture. On
Windows, the pipeline checks both the `MZ` marker and the PE signature to prove
the `.exe` suffix was not merely added to another file.

## Generate the relay project shape

```bash
copier copy --defaults \
  --data project_name=relay-service \
  --data package_name=relay relay-template /tmp/generated-relay
cd /tmp/generated-relay
pytest
```

Copier records the template source and answers in `.copier-answers.yml`. A
project generated from a versioned template can later use `copier update`, but
the resulting diff still needs review. The test gate renders a fresh project and
checks that its package preserves the relay resource and state contract.

For the optional compiler comparison, build the same entry point with Nuitka
onefile mode, then compare format, architecture, shared libraries, size and
startup behavior with the required PyInstaller artifact. Both outputs remain
specific to their target operating system and processor architecture.
## Python REPL debugging session

After the editable install, inspect the package actually loaded by Python:

```pycon
>>> import inspect
>>> import lab_02_package_build as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> [(name, type(getattr(lab, name)).__name__) for name in public]
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Compare the imported module path with the wheel and executable inputs. Inspect
one public callable signature before tracing how the same package enters each
artifact.

## Build and preview the documentation

Sphinx is the primary workflow. Its dependencies are already in the `dev`
extra in `pyproject.toml`; no separate requirements file is needed.

```bash
python -m sphinx -n -W --keep-going -b html docs build/docs/html
python -m sphinx -n -W --keep-going -b doctest docs build/docs/doctest
python -m http.server 8000 --bind 127.0.0.1 --directory build/docs/html
```

Open http://127.0.0.1:8000/ and inspect the API signatures, exception sections
and checkpoint footer. Stop the foreground preview with Ctrl-C. This loopback
server is for local inspection, not deployment.

`docs/conf.py` enables autodoc, autosummary, napoleon and doctest, with the
built-in classic theme. `docs/_templates/layout.html` extends the original
layout rather than copying the theme. The build imports the installed package;
an import that creates a cloud client would do so during documentation too.
The tests expose that mistake safely with a raising import in a scratch copy.

### Exercises

1. Use `help(executable_format)` and `inspect.signature(executable_format)`
   after importing it from `lab_02_package_build.binary`. Describe the
   guarantees a type hint cannot express.
2. Extend the `readelf_headers` docstring to distinguish missing input from
   a missing inspection tool. Keep the promise consistent with its exceptions.
   Explain why the PE offset comment is useful while “seek to offset” is not.
3. Add an unknown-signature example in `docs/examples.rst`. Build HTML and
   doctests, then make an expected result wrong in a scratch copy under `build/`
   and prove doctest returns nonzero. Never damage the source to test the gate.
4. Change the small template footer and verify the rendered page retains the
   original theme footer. Keep presentation separate from the API contract.

The test gate executes strict HTML and doctest builds, proves malformed
references and wrong examples fail, and retrieves generated pages from a
loopback server using an ephemeral port with cleanup. These are process and
HTTP checks, not assertions that source documentation contains certain words.

### Compare MkDocs optionally

```bash
pip install -e ".[dev,docs-comparison]"
python -m mkdocs build --strict --config-file mkdocs.yml
python -m mkdocs serve --dev-addr 127.0.0.1:8001 --config-file mkdocs.yml
```

Compare the Markdown guide in `mkdocs/index.md` with the imported Sphinx API.
MkDocs core does not execute the examples or generate this API reference.
Stop its preview with Ctrl-C. This optional comparison is not a required gate.

## Finishing condition

`pybootstrap check` must exit 0, including the documentation tests in pytest.
The HTML and doctest commands above must also exit 0 after your exercises.
Keep the existing wheel, sdist, template and platform-specific executable
evidence described above; documentation is an additional release artifact,
not a substitute for testing the produced command.

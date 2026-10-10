# Lab 02 Package Build

Build the `relayctl` Python command as standard Python distributions and as
native-looking executables for Linux and Windows. The executable contains a
Python interpreter; PyInstaller bundles the program but does not compile Python
to machine code.

The lab also contains `relay-template`, a Copier template that generates
the shared `/tasks` resource and queued, running, succeeded, and failed states.
Use the template when you start a later lab so the new project gets its own
package name. The generated package imports as `relay`; it does not relay traffic.
Its Sphinx site publishes the installed inspection API and runs examples
against it. The same build-and-test workflow can check SigRaft's API reference.

## Goal and working order

Turn a provided command into inspectable release artifacts, rather than assume
that an editable install proves a wheel or executable works. This `relayctl`
only inspects executable files; it does not submit jobs or contact `/tasks`.
Its contribution to the SigRaft job-management web service is the packaging
and documentation workflow, not a package imported by the final service.

Use Python 3.10 or later from this lab directory. Read the shared
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The command uses Python's standard library. The declared development extra
provides build, Twine, PyInstaller, Copier and Sphinx alongside the gate tools.
Linux binary inspection also needs `file` and `readelf`; Windows artifacts
must be built on Windows. Nuitka and MkDocs are optional comparisons.

1. Install below and run `relayctl --help` to identify the actual command.
2. Inspect the installed API in the REPL, then build the wheel and sdist.
3. Build the executable, inspect its format and run its `--version` command.
4. Generate a fresh project and run its tests, then complete the documentation
   exercises. Keep build outputs separate from the source checkout.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality gates

Run the configured checks before considering the lab complete:

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
| 2 | A gate could not run and supplied no verdict |

Fix findings reported by exit 1. For exit 2, repair the tool or its
configuration and rerun it; an unavailable check cannot establish a pass.

## Layout

```
src/lab_02_package_build/    the package
tests/                the test suite
ci/azure-pipelines.yml       Linux and Windows build jobs
relay-template/       updateable Copier template for the relay project contract
pyproject.toml        dependencies, tool settings and gate definition
```


## Build Python distributions

A source distribution contains the files needed to build the package. A wheel
is the installable package produced by that build. Create both, then check that
their metadata and long descriptions can be read by packaging tools:

```bash
python -m build
python -m twine check dist/*
```

Install the wheel into a clean virtual environment and run
`relayctl inspect /path/to/an/executable`
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

## Generate a relay project

Packaging distributes this command; a template starts a different project.
Use Copier to generate that project's names and shared job fields without
copying this lab's package name or build settings:

```bash
copier copy --defaults \
  --data project_name=relay-service \
  --data package_name=relay relay-template build/generated-relay
cd build/generated-relay
pytest
cd ../..
```

Copier saves the template source and answers in `.copier-answers.yml`. A
project generated from a versioned template can later use `copier update`, but
the resulting diff still needs review. The test gate renders a fresh project and
checks that its package preserves the relay resource and state contract.

The generated tests should pass before you edit the new project. That result
checks the generated contract, not the wheel or executable built above.

For the optional compiler comparison, return to this lab and build its entry
point with Nuitka onefile mode. Compare format, architecture, shared libraries,
size and startup behavior with the PyInstaller artifact. Both outputs remain
specific to their target operating system and processor architecture.

## Tests

A unit test checks one function or class on its own, with clocks, network,
storage and other dependencies replaced by deterministic fakes. A functional
test checks one complete feature through the lab's public interface, the way
a reader would use it.

`tests/test_lab_02_package_build.py` holds both kinds. Its unit tests check
`executable_format` and `readelf_headers` against small files in a temporary
directory, with a fake `readelf` where the tool must fail. Its functional tests
are `test_cli_reports_the_detected_format`,
`test_cli_rejects_elf_headers_for_another_format`,
`test_bundled_command_runs_without_the_source_tree`,
`test_bundled_command_has_the_native_format` and
`test_copier_template_generates_the_relay_contract`. They call the `relayctl`
entry point `main(argv)`, build and run a PyInstaller executable, and render
the relay template with the `copier` command.

`tests/test_documentation.py` holds functional tests for the documentation.
They run the Sphinx builders as child processes, and
`test_generated_site_is_served_on_loopback` fetches the built pages from a
server on 127.0.0.1.

```bash
pytest tests/test_lab_02_package_build.py -k "not cli and not bundled and not copier"
pytest tests/test_lab_02_package_build.py -k "cli or bundled or copier"
pytest tests/test_documentation.py
```

`pybootstrap check` runs both kinds of test in its test gate.

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
>>> from lab_02_package_build.binary import executable_format
>>> inspect.signature(executable_format)
>>> import sys
>>> executable_format(sys.executable).value
```

On Linux the last result is `ELF`; on Windows it is `PE`. Compare the imported
module path with the wheel and executable inputs. Format detection reads bytes
without executing the inspected program.

## Build and preview the documentation

Readers also need to know how the packaged API behaves. Sphinx builds that
reference from installed Python objects and executes selected examples.
Its dependencies are already in the `dev` extra in `pyproject.toml`;
no separate requirements file is needed.

```bash
python -m sphinx -n -W --keep-going -b html docs build/docs/html
python -m sphinx -n -W --keep-going -b doctest docs build/docs/doctest
python -m http.server 8000 --bind 127.0.0.1 --directory build/docs/html
```

Open http://127.0.0.1:8000/ and inspect the API signatures, exception sections
and lab footer. Stop the foreground preview with Ctrl-C. This loopback
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
   Explain why the PE offset comment is useful while "seek to offset" is not.
3. Add an unknown-signature example in `docs/examples.rst`. Build HTML and
   doctests, then make an expected result wrong in a scratch copy under `build/`
   and prove doctest returns nonzero. Never damage the source to test the gate.
4. Change the small template footer and verify the rendered page retains the
   original theme footer. Keep presentation separate from the API contract.

The test gate executes strict HTML and doctest builds, checks that malformed
references and wrong examples return nonzero, and retrieves generated pages from a
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
checks described above; documentation is an additional release artifact,
not a substitute for testing the produced command.

You should be able to explain source distributions, wheels and bundled
interpreters, and why the latter are platform-specific. Stop preview servers
with Ctrl-C. Remove only the generated project and build outputs you created
after keeping the results you need; no cloud resources are created.

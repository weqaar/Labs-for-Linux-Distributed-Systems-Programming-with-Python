# Lab 02 Package Build

Build the `relayctl` Python command as standard Python distributions and as
native-looking executables for Linux and Windows. The executable contains a
Python interpreter; PyInstaller bundles the program but does not compile Python
to machine code.

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

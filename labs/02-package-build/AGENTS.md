# Lab 02 Package Build

Development and check instructions for this lab.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means at least one gate
could not produce a verdict. Other gates may still have useful results.
Inspect the diagnostics, repair the affected check and rerun it before
reporting success.

## Layout

```
src/lab_02_package_build/    the package
tests/                the test suite
ci/azure-pipelines.yml       Linux and Windows executable builds
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- PyInstaller does not cross-compile. Build ELF files on Linux and PE files on
  Windows, then inspect the result before publishing it.
- Keep the Copier template tied to the relay `/tasks` resource and shared task
  states. Render it in a temporary directory during tests; never run template
  tasks from an untrusted source.
- Use `readelf`, not `ldd`, to inspect an untrusted ELF file. `ldd` can invoke a
  loader selected by the file.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, hiding the failed check from the pipeline.

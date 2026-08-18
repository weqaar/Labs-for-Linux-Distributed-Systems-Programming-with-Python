# Lab 02 Package Build

Orientation for anyone, human or AI, working in this repository.

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

Exit code 1 means a gate found problems. Exit code 2 means a gate could not
run, so nothing was checked. Treat 2 as more serious than 1: it says the
tooling is broken, and a broken checker reports nothing while looking fine.

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
- Use `readelf`, not `ldd`, to inspect an untrusted ELF file. `ldd` can invoke a
  loader selected by the file.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, which is worse than no gate at all because it looks like coverage.

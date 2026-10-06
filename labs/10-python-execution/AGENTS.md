# Lab 10 Python Execution

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
src/lab_10_python_execution/    the package
tests/                the test suite
```

## Conventions

- Dependencies belong in `pyproject.toml`. There is no second file describing
  the build, and adding one would create two descriptions that drift apart.
- Gate settings live in `[tool.pybootstrap]`. Tool settings live in each tool's
  own table, so every tool stays usable on its own.
- Source inspection must compile but never execute caller-provided text.
- Keep the RISC-V scope to the RV32I register-register ADD used in the chapter.
  The trace must expose instruction fields, registers, PC movement, and all 32
  one-bit full-adder stages.
- Preserve register x0 as hard-wired zero and wrap arithmetic to 32 bits.
- Keep teaching instructions fixed at one opcode byte and one operand byte.
- Add instruction definitions once in the shared contract table so assembly,
  disassembly, stack effects, and execution cannot drift.
- Keep architecture reporting descriptive. Python bytecode portability does
  not imply that native extension modules are portable.
- Preserve `FORMAT_TASK_ID` as one-input, one-output behavior with net stack
  effect zero and the shared `task-<positive integer>` contract.
- Do not add `|| true` to a CI check. It converts a broken gate into a green
  build, hiding the failed check from the pipeline.

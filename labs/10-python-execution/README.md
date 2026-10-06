# Lab 10 Python Execution

This lab makes Python execution visible before later labs add threads,
processes, native extensions, or a modified CPython interpreter. The Python
import name in those labs is `relay`. That name does not mean the program
relays traffic. You inspect source without executing it and run one
job-identifier program on a small virtual machine written for this exercise.
Its instructions have a fixed width so each step is easy to see.

## Goal and preparation

Explain each representation between Python text and execution rather than
equate bytecode with CPU instructions. You will inspect provided code, run two
small programs that simulate processors, and reject malformed programs.
Use Python 3.10 or later from this directory and read
[`CODING_STANDARDS.md`](../CODING_STANDARDS.md) and `AGENTS.md`.
The runtime uses only the standard library, including `tokenize`, `ast` and
`dis`; development dependencies are declared in `pyproject.toml`. Neither a
RISC-V board nor a custom interpreter is needed here.

A token identifies a piece of source text. An abstract syntax tree (AST)
records how those pieces form statements and expressions. A code object holds
the compiled Python instructions and related values. The exercise virtual
machine below uses a much smaller instruction set to make execution
steps visible. It is not the CPython virtual machine.

## Capability added here

- tokenize source and inspect its abstract syntax tree
- compile source into a code object without executing it
- inspect CPython instructions, constants, names and bytecode magic
- report the interpreter, operating system, machine architecture and native
  extension suffixes as separate compatibility boundaries
- encode and simulate `add x3, x1, x2` as RV32I word `0x002081b3`
- trace the simulated instruction through 32 one-bit digital full adders
- compare a register machine with a stack machine
- assemble and execute numeric exercise opcodes on an operand stack with
  declared stack effects
- implement `FORMAT_TASK_ID`, which turns integer `17` into `task-17`
- reject unknown opcodes, invalid operands, stack underflow and a missing return

The exercise format uses two bytes per instruction: one byte for the opcode and
one byte for its argument. It is intentionally smaller than CPython. Lab 15
later changes the real CPython 3.14.7 instruction set and rebuilds the
interpreter.

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

Each gate can also be run directly, because pybootstrap does not wrap or
reconfigure them:

```bash
ruff format --check src tests
ruff check src tests
pyright src tests
pytest
```

## Layout

```
src/lab_10_python_execution/    the package
tests/                the test suite
pyproject.toml        dependencies, tool settings and gate definition
```

## Follow the representations

The arithmetic example connects the two simulated processors through one result,
the task number 17. Run `relay_addition_trace()` first and inspect how registers `x1=8` and `x2=9`
produce task number 17 in `x3`. Then run `relay_task_program()` through
`MiniVirtualMachine` and compare the physical ISA model with the Python stack
VM. Its operand-stack entries are references to Python objects. Use
`inspect_source()` on a small function and compare tokens, AST nodes,
code-object fields and `dis` instructions. This comparison prepares you for
Lab 15, where adding an instruction requires rebuilding CPython rather than
changing the exercise virtual machine.
## Python REPL debugging session

Use the prompt to inspect tokens and the AST tree without executing the source:

```pycon
>>> import lab_10_python_execution as lab
>>> lab.__name__, lab.__file__
>>> trace = lab.relay_addition_trace()
>>> hex(trace.instruction), trace.result, trace.pc_after
('0x2081b3', 17, 4)
>>> trace.adder_stages[:5]
>>> tokens = lab.tokenize_source("if ready:\n    state = 'running'\n")
>>> [(token.kind, token.text, token.line, token.column) for token in tokens]
>>> report = lab.inspect_source("result = relay_transition('running', True)")
>>> [(node.depth, node.path, node.kind) for node in report.ast_nodes]
>>> help(lab.MiniVirtualMachine)
```

The trace produces 17 and advances the program counter (PC) by four bytes,
the width of the simulated RISC-V instruction. The later examples do something
different: they inspect Python source without running it. The AST outline
visits parents before children; each path names the parent field and list index.

## Experiment and completion

1. Run the REPL examples and find the instruction, register and PC fields in
   `riscv.py`. Explain why the 32-bit arithmetic wraps.
2. Read `mini_vm.py` and its instruction table. In a test, remove the return
   instruction or supply an invalid operand and check the explicit failure.
3. Run `pytest -q tests/test_lab_10_python_execution.py`, then
   `pybootstrap check`, which must exit 0. Exit 1 reports findings; exit 2
   reports a gate that could not run.

This helps you distinguish Python code from processor-specific artifacts when
packaging the SigRaft job-management web service. Lab 39 does not import this
VM. The source inspector compiles text but does not execute it; the RV32I
simulator is not hardware.
Exit the REPL to discard model state. No network or Azure resource is created.

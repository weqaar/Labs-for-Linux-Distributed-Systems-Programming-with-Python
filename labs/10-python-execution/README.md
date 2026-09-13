# Lab 10 Python Execution

This checkpoint makes Python execution visible before relay reaches threads,
processes, native extensions, or a modified CPython interpreter. It inspects
source without executing it and runs one relay task-ID program on a small,
fixed-width teaching VM.

## Capability added here

- tokenize source and inspect its abstract syntax tree
- compile source into a code object without executing it
- inspect CPython instructions, constants, names and bytecode magic
- report the interpreter, operating system, machine architecture and native
  extension suffixes as separate compatibility boundaries
- encode and simulate `add x3, x1, x2` as RV32I word `0x002081b3`
- trace the simulated instruction through 32 one-bit digital full adders
- compare a register machine with a stack machine
- assemble and execute numeric teaching opcodes on an operand stack with
  declared stack effects
- implement `FORMAT_TASK_ID`, which turns integer `17` into `task-17`
- reject unknown opcodes, invalid operands, stack underflow and a missing return

The teaching format uses two bytes per instruction: one byte for the opcode and
one byte for its argument. It is intentionally smaller than CPython. Lab 15
later changes the real CPython 3.14.7 instruction set and rebuilds the
interpreter.

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

Run `relay_addition_trace()` first and inspect how registers `x1=8` and `x2=9`
produce task number 17 in `x3`. Then run `relay_task_program()` through
`MiniVirtualMachine` and compare the physical ISA model with the Python stack
VM. Its operand-stack entries are references to Python objects. Use
`inspect_source()` on a small function and compare tokens, AST nodes,
code-object fields and `dis` instructions. The final relay release records
this source-to-bytecode boundary before it records the custom CPython
instruction.
## Python REPL debugging session

Use the prompt to inspect tokens and the AST tree without executing the source:

```pycon
>>> import lab_10_python_execution as lab
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

The AST outline is a preorder tree. Each path records the parent field and list
index, while depth and source position make the parsed structure testable.

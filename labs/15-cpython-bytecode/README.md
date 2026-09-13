# Lab 15 Engineering the CPython bytecode machine

This checkpoint gives `relay` a typed task-query language and a source-built
CPython operation for formatting task identifiers. It separates two jobs that
are often confused: PyParsing turns operator input into a typed expression
tree, while the modified interpreter executes a new `RELAY_TASK_ID`
instruction.

The normal lab gate is local and deterministic. Building CPython is a separate,
deliberate exercise because it needs a compiler toolchain and more time.

## Exercise 1: Parse a relay query

Create the environment and run the gate:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pybootstrap check
```

Read `query.py`, then try queries such as:

```text
state = queued or priority >= 7 and action contains "index"
not (state = failed or action contains "archive") and priority > 5
```

The grammar distinguishes numeric and text fields before evaluation. Parse
actions construct frozen `Predicate`, `Not`, `And`, and `Or` nodes. They do not
perform I/O. `parse_all=True` rejects a valid prefix followed by unparsed text,
and the public error reports the input column.

Add one parser test that proves `and` binds more tightly than `or`, and another
that proves parentheses change the result. Do not use `eval`; the typed tree is
the security boundary between operator text and task selection.

## Exercise 2: Inspect the pinned source patch

The exercise uses CPython 3.14.7, tag `v3.14.7`, at commit
`823f0323ee6ec1402088b73bce1a38473cac36dc`. The exact source and generated
files are recorded in `artifacts/source-manifest.json`.

Clone and verify that revision:

```bash
git clone --depth 1 --branch v3.14.7 https://github.com/python/cpython.git cpython-relay
git -C cpython-relay rev-parse HEAD
git -C cpython-relay apply --check \
  ../artifacts/cpython-3.14.7-relay-opcode.patch
git -C cpython-relay apply \
  ../artifacts/cpython-3.14.7-relay-opcode.patch
```

Review the patch before building it. Follow the path from direct call
recognition in `Python/codegen.c`, through the instruction definition in
`Python/bytecodes.c`, to the ordinary callable implementation in
`Python/bltinmodule.c`. Generated headers are outputs, not places to design the
instruction.

## Exercise 3: Generate and build

From the patched CPython checkout:

```bash
python3 Tools/clinic/clinic.py -f Python/bltinmodule.c
make regen-cases regen-opcode regen-opcode-targets
./configure --with-pydebug
make -j4
```

The checked-in patch includes generated files so its changes can be reviewed,
but regeneration must reproduce them. The custom magic number is 3628 because
adding an opcode changes the bytecode format.

## Exercise 4: Prove both execution paths

Run the custom and affected upstream tests:

```bash
./python -m test -v test_relay_opcode
./python -m test -j4 \
  test_builtin test_compile test_dis test_importlib.test_util
```

Then inspect a direct call:

```bash
./python - <<'PY'
import dis

def task_id(number):
    return relay_task_id(number)

dis.dis(task_id)
print(task_id(17))
PY
```

The disassembly must contain `RELAY_TASK_ID`, not `CALL`, and the output must
be `task-17`. An alias such as `factory = builtins.relay_task_id` follows the
ordinary builtin call path and therefore contains `CALL`.

## Exercise 5: Test the semantic boundary

The compiler treats a direct bare-name call to `relay_task_id` as an intrinsic.
Local shadowing does not replace that direct operation. Keyword calls, starred
arguments, and calls with the wrong arity remain ordinary calls and receive the
builtin argument checks. Confirm all of these cases before changing compiler
recognition.

The instruction accepts exact, non-negative Python integers. It rejects
negative integers, strings, and booleans, while preserving arbitrary-precision
integers. The nested-expression test verifies that consuming one stack value
and producing one result does not damage adjacent values.

## Architecture checkpoint

Build the same patch on another supported processor when one is available.
`RELAY_TASK_ID` remains CPython bytecode, while the C compiler emits different
native instructions for each host. The `.pyc` format is tied to the custom
interpreter magic, and the executable interpreter is tied to its operating
system and processor architecture.

## Layout

```text
src/lab_15_cpython_bytecode/query.py   typed PyParsing grammar and evaluator
src/lab_15_cpython_bytecode/source.py  source pin and build contracts
artifacts/*.patch                      reviewed CPython source change
artifacts/source-manifest.json         source, generation, build, and test record
tests/                                 deterministic parser and patch checks
```
## Python REPL debugging session

Inspect the parser and ordinary interpreter before building custom CPython:

```pycon
>>> import inspect
>>> import lab_15_cpython_bytecode as lab
>>> lab.__name__, lab.__file__
>>> public = [name for name in dir(lab) if not name.startswith("_")]
>>> public
>>> inspect.getmembers(lab, inspect.isclass)
>>> help(lab)
```

Parse one query, inspect the frozen expression-node types, and disassemble the
ordinary callable path. Repeat under the patched interpreter and compare the
direct custom instruction.

# Lab 15 Engineering the CPython bytecode machine

## Checks

```bash
pip install -e ".[dev]"
pybootstrap check
```

The normal gate must not clone or build CPython. It validates the parser,
source pin, build plan, and committed patch artifact without network access.

## Source invariants

- Keep the source at CPython tag `v3.14.7`, commit
  `823f0323ee6ec1402088b73bce1a38473cac36dc`.
- Treat `Python/bytecodes.c` as the opcode source of truth. Regenerate derived
  files using CPython's generators.
- Keep `RELAY_TASK_ID` as a tier-one instruction with one input, one output,
  and net stack effect zero.
- Accept exact non-negative integers, reject booleans, and preserve arbitrary
  precision in both the opcode and builtin paths.
- Preserve the documented compiler-intrinsic rule: only a direct bare-name call
  with one positional argument emits the custom instruction.
- Bump bytecode magic whenever the instruction set changes.
- Export only a reviewable patch and manifest. Never commit a CPython checkout
  or depend on a private filesystem path.

## Parser invariants

- Parse into typed immutable nodes before evaluating a query.
- Keep text and numeric operators field-specific.
- Consume the complete input and retain useful syntax locations.
- Parse actions must not perform I/O or execute input as Python.
- Keep local gates deterministic and independent of an Azure subscription.

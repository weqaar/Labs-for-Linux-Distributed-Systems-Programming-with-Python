"""Safe interpreter inspection helpers."""

from __future__ import annotations

import dis
import inspect
import types
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ObjectSnapshot:
    type_name: str
    identity: int
    attributes: tuple[str, ...]
    instance_state: dict[str, str]
    call_signature: str | None


@dataclass(frozen=True, slots=True)
class BytecodeRow:
    offset: int
    operation: str
    argument: str
    starts_line: int | None


def inspect_object(value: object) -> ObjectSnapshot:
    """Capture the facts commonly queried in an interactive Python session."""

    namespace = vars(value) if hasattr(value, "__dict__") else {}
    state = {str(name): repr(item) for name, item in namespace.items()}
    signature = None
    if callable(value):
        try:
            signature = str(inspect.signature(value))
        except (TypeError, ValueError):
            pass
    return ObjectSnapshot(
        type_name=f"{type(value).__module__}.{type(value).__qualname__}",
        identity=id(value),
        attributes=tuple(dir(value)),
        instance_state=state,
        call_signature=signature,
    )


def inspect_bytecode(function: types.FunctionType) -> tuple[BytecodeRow, ...]:
    """Return version-specific CPython instructions as structured evidence."""

    return tuple(
        BytecodeRow(
            offset=instruction.offset,
            operation=instruction.opname,
            argument=instruction.argrepr,
            starts_line=instruction.starts_line,
        )
        for instruction in dis.get_instructions(function)
    )


def inspect_mapping(mapping: dict[str, Any], key: str) -> tuple[bool, Any]:
    """Distinguish an absent mapping key from a key whose value is None."""

    marker = object()
    value = mapping.get(key, marker)
    return (value is not marker, None if value is marker else value)

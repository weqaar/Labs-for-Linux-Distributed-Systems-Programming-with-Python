"""Beginner-visible models of Python source, bytecode, and execution."""

from __future__ import annotations

from .execution import (
    ArchitectureReport,
    AstNode,
    FunctionBytecode,
    SourceReport,
    SourceToken,
    architecture_report,
    inspect_function,
    inspect_source,
    relay_transition,
    tokenize_source,
)
from .mini_vm import (
    InstructionDefinition,
    MiniOpcode,
    MiniVirtualMachine,
    assemble,
    instruction_set,
    relay_task_program,
)
from .riscv import (
    FullAdderStage,
    RiscVAddTrace,
    RiscVSimulator,
    encode_add,
    relay_addition_trace,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ArchitectureReport",
    "AstNode",
    "FunctionBytecode",
    "FullAdderStage",
    "InstructionDefinition",
    "MiniOpcode",
    "MiniVirtualMachine",
    "RiscVAddTrace",
    "RiscVSimulator",
    "SourceReport",
    "SourceToken",
    "architecture_report",
    "assemble",
    "encode_add",
    "inspect_function",
    "inspect_source",
    "instruction_set",
    "relay_task_program",
    "relay_addition_trace",
    "relay_transition",
    "tokenize_source",
]

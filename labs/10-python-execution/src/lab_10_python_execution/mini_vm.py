"""A tiny stack machine that makes opcode mechanics explicit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class MiniOpcode(IntEnum):
    """Numeric operation codes understood by the teaching VM."""

    LOAD_CONST = 1
    ADD = 2
    FORMAT_TASK_ID = 3
    RETURN = 4


@dataclass(frozen=True, slots=True)
class InstructionDefinition:
    """The contract that gives one numeric opcode its meaning."""

    opcode: MiniOpcode
    argument_bytes: int
    pops: int
    pushes: int
    summary: str

    @property
    def stack_effect(self) -> int:
        return self.pushes - self.pops


instruction_set = {
    MiniOpcode.LOAD_CONST: InstructionDefinition(
        MiniOpcode.LOAD_CONST,
        argument_bytes=1,
        pops=0,
        pushes=1,
        summary="push the object referenced by constants[argument]",
    ),
    MiniOpcode.ADD: InstructionDefinition(
        MiniOpcode.ADD,
        argument_bytes=1,
        pops=2,
        pushes=1,
        summary="pop two integer operands and push their sum",
    ),
    MiniOpcode.FORMAT_TASK_ID: InstructionDefinition(
        MiniOpcode.FORMAT_TASK_ID,
        argument_bytes=1,
        pops=1,
        pushes=1,
        summary="replace a non-negative integer with task-N",
    ),
    MiniOpcode.RETURN: InstructionDefinition(
        MiniOpcode.RETURN,
        argument_bytes=1,
        pops=1,
        pushes=0,
        summary="return the object referenced by the top stack item",
    ),
}


def assemble(instructions: tuple[tuple[MiniOpcode, int], ...]) -> bytes:
    """Encode fixed-width opcode and argument pairs."""

    encoded = bytearray()
    for opcode, argument in instructions:
        if not 0 <= argument <= 255:
            raise ValueError("instruction argument must fit in one byte")
        encoded.extend((opcode, argument))
    return bytes(encoded)


def relay_task_program(constant_index: int = 0) -> bytes:
    """Build the teaching program that returns a relay task identifier."""

    return assemble(
        (
            (MiniOpcode.LOAD_CONST, constant_index),
            (MiniOpcode.FORMAT_TASK_ID, 0),
            (MiniOpcode.RETURN, 0),
        )
    )


class MiniVirtualMachine:
    """Decode bytecode and execute instructions on an operand stack."""

    def execute(self, bytecode: bytes, constants: tuple[object, ...]) -> object:
        if len(bytecode) % 2:
            raise ValueError("bytecode must contain opcode and argument pairs")
        operand_stack: list[object] = []
        instruction_pointer = 0
        while instruction_pointer < len(bytecode):
            numeric_opcode = bytecode[instruction_pointer]
            argument = bytecode[instruction_pointer + 1]
            instruction_pointer += 2
            try:
                opcode = MiniOpcode(numeric_opcode)
            except ValueError as exc:
                raise ValueError(f"unknown opcode: {numeric_opcode}") from exc
            definition = instruction_set[opcode]
            if len(operand_stack) < definition.pops:
                raise RuntimeError(f"stack underflow in {opcode.name}")
            if opcode is MiniOpcode.LOAD_CONST:
                try:
                    operand_stack.append(constants[argument])
                except IndexError as exc:
                    raise ValueError(f"constant index is out of range: {argument}") from exc
            elif opcode is MiniOpcode.ADD:
                right = operand_stack.pop()
                left = operand_stack.pop()
                if not isinstance(left, int) or not isinstance(right, int):
                    raise TypeError("ADD requires two integers")
                operand_stack.append(left + right)
            elif opcode is MiniOpcode.FORMAT_TASK_ID:
                value = operand_stack.pop()
                if type(value) is not int:
                    raise TypeError("FORMAT_TASK_ID requires an exact integer")
                if value < 0:
                    raise ValueError("FORMAT_TASK_ID requires a non-negative integer")
                operand_stack.append(f"task-{value}")
            elif opcode is MiniOpcode.RETURN:
                return operand_stack.pop()
        raise RuntimeError("program ended without RETURN")

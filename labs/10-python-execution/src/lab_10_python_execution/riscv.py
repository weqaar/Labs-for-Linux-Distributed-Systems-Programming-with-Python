"""A small RV32I ADD simulator with a digital full-adder trace."""

from __future__ import annotations

from dataclasses import dataclass

_OP = 0b0110011
_ADD_FUNCT3 = 0b000
_ADD_FUNCT7 = 0b0000000
_WORD_MASK = (1 << 32) - 1


@dataclass(frozen=True, slots=True)
class FullAdderStage:
    """Inputs and outputs of one one-bit full adder."""

    bit: int
    left: int
    right: int
    carry_in: int
    sum_bit: int
    carry_out: int


@dataclass(frozen=True, slots=True)
class RiscVAddTrace:
    """Decoded fields and datapath evidence for one RV32I ADD."""

    instruction: int
    pc_before: int
    pc_after: int
    rs1: int
    rs2: int
    rd: int
    left: int
    right: int
    result: int
    adder_stages: tuple[FullAdderStage, ...]


def encode_add(*, rd: int, rs1: int, rs2: int) -> int:
    """Encode ``add rd, rs1, rs2`` as one RV32I R-type word."""

    for name, register in (("rd", rd), ("rs1", rs1), ("rs2", rs2)):
        if type(register) is not int or not 0 <= register < 32:
            raise ValueError(f"{name} must be an integer register from 0 to 31")
    return (_ADD_FUNCT7 << 25) | (rs2 << 20) | (rs1 << 15) | (_ADD_FUNCT3 << 12) | (rd << 7) | _OP


class RiscVSimulator:
    """Execute the RV32I ADD instruction needed by this checkpoint."""

    def __init__(self) -> None:
        self._registers = [0] * 32
        self._pc = 0

    def set_register(self, register: int, value: int) -> None:
        """Set one register while preserving RV32I's hard-wired x0."""

        _validate_register(register)
        if type(value) is not int:
            raise TypeError("register value must be an exact integer")
        if register != 0:
            self._registers[register] = value & _WORD_MASK

    def read_register(self, register: int) -> int:
        """Read one unsigned 32-bit register value."""

        _validate_register(register)
        return self._registers[register]

    def execute_add(self, instruction: int) -> RiscVAddTrace:
        """Decode and execute one RV32I ADD instruction."""

        if type(instruction) is not int or not 0 <= instruction <= _WORD_MASK:
            raise ValueError("instruction must be one unsigned 32-bit integer")
        opcode = instruction & 0x7F
        rd = (instruction >> 7) & 0x1F
        funct3 = (instruction >> 12) & 0x7
        rs1 = (instruction >> 15) & 0x1F
        rs2 = (instruction >> 20) & 0x1F
        funct7 = (instruction >> 25) & 0x7F
        if (opcode, funct3, funct7) != (_OP, _ADD_FUNCT3, _ADD_FUNCT7):
            raise ValueError("instruction is not an RV32I ADD")

        left = self._registers[rs1]
        right = self._registers[rs2]
        result, stages = _add_u32(left, right)
        pc_before = self._pc
        self._pc += 4
        if rd != 0:
            self._registers[rd] = result
        return RiscVAddTrace(
            instruction=instruction,
            pc_before=pc_before,
            pc_after=self._pc,
            rs1=rs1,
            rs2=rs2,
            rd=rd,
            left=left,
            right=right,
            result=result,
            adder_stages=stages,
        )


def relay_addition_trace() -> RiscVAddTrace:
    """Run ``add x3, x1, x2`` so 8 and 9 produce relay task number 17."""

    simulator = RiscVSimulator()
    simulator.set_register(1, 8)
    simulator.set_register(2, 9)
    return simulator.execute_add(encode_add(rd=3, rs1=1, rs2=2))


def _validate_register(register: int) -> None:
    if type(register) is not int or not 0 <= register < 32:
        raise ValueError("register must be an integer from 0 to 31")


def _add_u32(left: int, right: int) -> tuple[int, tuple[FullAdderStage, ...]]:
    result = 0
    carry = 0
    stages: list[FullAdderStage] = []
    for bit in range(32):
        left_bit = (left >> bit) & 1
        right_bit = (right >> bit) & 1
        sum_bit = left_bit ^ right_bit ^ carry
        carry_out = (left_bit & right_bit) | (carry & (left_bit ^ right_bit))
        stages.append(
            FullAdderStage(
                bit=bit,
                left=left_bit,
                right=right_bit,
                carry_in=carry,
                sum_bit=sum_bit,
                carry_out=carry_out,
            )
        )
        result |= sum_bit << bit
        carry = carry_out
    return result, tuple(stages)

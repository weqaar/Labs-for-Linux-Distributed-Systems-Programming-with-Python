"""Functional tests for checkpoint 10.

These tests drive the public package interface of `lab_10_python_execution`
from start to finish: the RV32I simulator computes a task number, the exercise
virtual machine turns it into a `task-N` identifier, and the source inspector
tokenizes, parses, compiles and disassembles code without running it. Each
feature is also given a malformed program a reader could write.
"""

# pyright: strict

from __future__ import annotations

from pathlib import Path

import pytest

from lab_10_python_execution import (
    MiniOpcode,
    MiniVirtualMachine,
    RiscVSimulator,
    assemble,
    encode_add,
    inspect_function,
    inspect_source,
    relay_task_program,
    relay_transition,
)


def test_register_sum_becomes_a_task_identifier_on_the_stack_machine() -> None:
    simulator = RiscVSimulator()
    simulator.set_register(1, 8)
    simulator.set_register(2, 9)

    trace = simulator.execute_add(encode_add(rd=3, rs1=1, rs2=2))
    task_id = MiniVirtualMachine().execute(relay_task_program(), (simulator.read_register(3),))

    assert trace.instruction == 0x002081B3
    assert (trace.pc_before, trace.pc_after) == (0, 4)
    assert task_id == "task-17"


def test_register_sum_wraps_at_32_bits_before_formatting() -> None:
    simulator = RiscVSimulator()
    simulator.set_register(1, 0xFFFF_FFFF)
    simulator.set_register(2, 18)

    trace = simulator.execute_add(encode_add(rd=3, rs1=1, rs2=2))

    assert trace.result == 17
    assert trace.adder_stages[31].carry_out == 1
    assert MiniVirtualMachine().execute(relay_task_program(), (trace.result,)) == "task-17"
    with pytest.raises(ValueError, match="not an RV32I ADD"):
        simulator.execute_add(0x0000_0013)


def test_program_without_return_is_rejected_after_running_its_instructions() -> None:
    program = assemble(
        (
            (MiniOpcode.LOAD_CONST, 0),
            (MiniOpcode.LOAD_CONST, 1),
            (MiniOpcode.ADD, 0),
            (MiniOpcode.FORMAT_TASK_ID, 0),
        )
    )

    with pytest.raises(RuntimeError, match="without RETURN"):
        MiniVirtualMachine().execute(program, (8, 9))
    with pytest.raises(ValueError, match="constant index is out of range"):
        MiniVirtualMachine().execute(relay_task_program(constant_index=3), (17,))


def test_source_is_inspected_and_compiled_without_being_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    source = f"open({str(marker)!r}, 'w').write('ran')\n"

    report = inspect_source(source)
    function = inspect_function(relay_transition)

    assert not marker.exists()
    assert report.ast_root == "Module"
    assert "open" in report.names
    assert str(marker) in report.constants
    assert function.name == "relay_transition"
    assert {"running", "succeeded", "failed"} <= set(
        constant for constant in function.constants if isinstance(constant, str)
    )
    with pytest.raises(SyntaxError):
        inspect_source("state = = 'running'\n")

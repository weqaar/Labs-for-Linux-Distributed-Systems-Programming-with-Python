"""Tests for Python execution representations and the teaching VM."""

from __future__ import annotations

import pytest

from lab_10_python_execution import (
    MiniOpcode,
    MiniVirtualMachine,
    RiscVSimulator,
    __version__,
    architecture_report,
    assemble,
    encode_add,
    inspect_function,
    inspect_source,
    instruction_set,
    relay_addition_trace,
    relay_task_program,
    relay_transition,
    tokenize_source,
)


def test_riscv_add_encoding_matches_the_documented_instruction_word() -> None:
    assert encode_add(rd=3, rs1=1, rs2=2) == 0x002081B3


def test_riscv_simulator_traces_registers_pc_and_digital_adder() -> None:
    trace = relay_addition_trace()

    assert (trace.rs1, trace.rs2, trace.rd) == (1, 2, 3)
    assert (trace.left, trace.right, trace.result) == (8, 9, 17)
    assert (trace.pc_before, trace.pc_after) == (0, 4)
    assert len(trace.adder_stages) == 32
    assert trace.adder_stages[0].sum_bit == 1
    assert trace.adder_stages[3].carry_out == 1


def test_riscv_x0_is_hard_wired_to_zero() -> None:
    simulator = RiscVSimulator()
    simulator.set_register(0, 99)
    simulator.set_register(1, 8)
    simulator.execute_add(encode_add(rd=0, rs1=1, rs2=1))

    assert simulator.read_register(0) == 0


def test_riscv_simulator_rejects_bad_registers_and_non_add_instructions() -> None:
    with pytest.raises(ValueError, match="rd"):
        encode_add(rd=32, rs1=1, rs2=2)
    with pytest.raises(ValueError, match="RV32I ADD"):
        RiscVSimulator().execute_add(0)


def test_source_moves_through_tokens_ast_and_module_code() -> None:
    report = inspect_source("result = relay_transition('running', True)")

    assert tuple((token.kind, token.text) for token in report.tokens[:3]) == (
        ("NAME", "result"),
        ("OP", "="),
        ("NAME", "relay_transition"),
    )
    assert report.ast_root == "Module"
    assert report.ast_nodes[0].path == "root"
    assert report.ast_nodes[0].kind == "Module"
    assert report.names == ("relay_transition", "result")
    assert "running" in report.constants


def test_tokenization_exposes_indentation_as_structure_before_parsing() -> None:
    tokens = tokenize_source("if ready:\n    state = 'running'\n")

    assert tuple(token.kind for token in tokens) == (
        "NAME",
        "NAME",
        "OP",
        "INDENT",
        "NAME",
        "OP",
        "STRING",
        "DEDENT",
    )
    assert tokens[3].text == "    "
    assert (tokens[4].line, tokens[4].column) == (2, 4)


def test_ast_outline_is_a_preorder_tree_with_paths_and_source_locations() -> None:
    report = inspect_source(
        "def complete(state):\n"
        "    if state == 'running':\n"
        "        return 'succeeded'\n"
        "    return 'failed'\n"
    )
    by_path = {node.path: node for node in report.ast_nodes}

    assert by_path["root"].kind == "Module"
    assert by_path["root.body[0]"].kind == "FunctionDef"
    assert by_path["root.body[0].body[0]"].kind == "If"
    assert by_path["root.body[0].body[0].test"].kind == "Compare"
    assert by_path["root.body[0].body[0].body[0]"].kind == "Return"
    assert by_path["root.body[0].body[0]"].depth == 2
    assert by_path["root.body[0].body[0]"].line == 2


def test_python_function_exposes_code_object_and_decoded_instructions() -> None:
    report = inspect_function(relay_transition)
    operation_names = tuple(instruction.opname for instruction in report.instructions)

    assert report.name == "relay_transition"
    assert report.positional_arguments == 2
    assert report.local_names[:2] == ("state", "succeeded")
    assert "RETURN_VALUE" in operation_names
    assert "running" in report.constants


def test_architecture_report_separates_bytecode_and_native_extensions() -> None:
    report = architecture_report()

    assert report.implementation
    assert len(report.bytecode_magic) == 4
    assert report.machine
    assert report.operating_system
    assert report.extension_suffixes


def test_opcode_definition_states_numeric_identity_and_stack_effect() -> None:
    definition = instruction_set[MiniOpcode.FORMAT_TASK_ID]

    assert int(definition.opcode) == 3
    assert definition.argument_bytes == 1
    assert definition.pops == definition.pushes == 1
    assert definition.stack_effect == 0


def test_teaching_vm_decodes_and_executes_custom_task_instruction() -> None:
    bytecode = relay_task_program()

    assert bytecode == bytes((1, 0, 3, 0, 4, 0))
    assert MiniVirtualMachine().execute(bytecode, (17,)) == "task-17"


def test_teaching_vm_adds_before_formatting_task_identifier() -> None:
    bytecode = assemble(
        (
            (MiniOpcode.LOAD_CONST, 0),
            (MiniOpcode.LOAD_CONST, 1),
            (MiniOpcode.ADD, 0),
            (MiniOpcode.FORMAT_TASK_ID, 0),
            (MiniOpcode.RETURN, 0),
        )
    )

    assert MiniVirtualMachine().execute(bytecode, (8, 9)) == "task-17"


@pytest.mark.parametrize(
    ("bytecode", "constants", "message"),
    [
        (bytes((99, 0)), (), "unknown opcode"),
        (bytes((MiniOpcode.ADD, 0)), (), "stack underflow"),
        (relay_task_program(), (-1,), "non-negative"),
        (relay_task_program(), (True,), "exact integer"),
        (bytes((MiniOpcode.LOAD_CONST,)), (), "opcode and argument"),
    ],
)
def test_teaching_vm_rejects_invalid_programs(
    bytecode: bytes,
    constants: tuple[object, ...],
    message: str,
) -> None:
    with pytest.raises((RuntimeError, TypeError, ValueError), match=message):
        MiniVirtualMachine().execute(bytecode, constants)


def test_relay_transition_retains_product_state_contract() -> None:
    assert relay_transition("running", True) == "succeeded"
    assert relay_transition("running", False) == "failed"
    with pytest.raises(ValueError, match="running"):
        relay_transition("queued", True)


def test_public_inputs_fail_explicitly() -> None:
    with pytest.raises(ValueError, match="source"):
        inspect_source(" ")
    with pytest.raises(TypeError, match="Python function"):
        inspect_function(len)
    with pytest.raises(ValueError, match="one byte"):
        assemble(((MiniOpcode.LOAD_CONST, 256),))


def test_version_is_exposed() -> None:
    assert __version__

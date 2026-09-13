"""Tests for the parser and custom CPython source-build contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lab_15_cpython_bytecode import (
    CPYTHON_COMMIT,
    CPYTHON_TAG,
    And,
    CPythonSource,
    Or,
    QuerySyntaxError,
    build_plan,
    parse_query,
    select_tasks,
    verify_patch_contract,
)


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    action: str
    state: str
    priority: int
    attempts: int


TASKS = [
    Task("task-17", "index /srv/a", "queued", 9, 0),
    Task("task-18", "archive /srv/b", "running", 3, 1),
    Task("task-19", "index /srv/c", "failed", 7, 3),
]


def test_pyparsing_builds_typed_expression_with_precedence() -> None:
    expression = parse_query('state = queued or priority >= 7 and action contains "index"')

    assert isinstance(expression, Or)
    assert isinstance(expression.expressions[1], And)
    assert [task.task_id for task in select_tasks(TASKS, "state = queued or priority >= 7")] == [
        "task-17",
        "task-19",
    ]


def test_parentheses_and_not_override_default_precedence() -> None:
    selected = select_tasks(
        TASKS,
        'not (state = failed or action contains "archive") and priority > 5',
    )

    assert tuple(task.task_id for task in selected) == ("task-17",)


def test_numeric_and_text_operators_remain_type_specific() -> None:
    assert tuple(task.task_id for task in select_tasks(TASKS, "attempts >= 1")) == (
        "task-18",
        "task-19",
    )
    assert tuple(task.task_id for task in select_tasks(TASKS, "task_id != task-18")) == (
        "task-17",
        "task-19",
    )
    with pytest.raises(QuerySyntaxError, match="invalid query"):
        parse_query("priority contains 7")


def test_parser_consumes_the_complete_input_and_reports_location() -> None:
    with pytest.raises(QuerySyntaxError, match="column"):
        parse_query("state = queued remove everything")


def test_cpython_source_is_pinned_by_release_and_commit() -> None:
    source = CPythonSource()
    destination = Path("/tmp/cpython-relay")

    assert CPYTHON_TAG == "v3.14.7"
    assert CPYTHON_COMMIT == "823f0323ee6ec1402088b73bce1a38473cac36dc"
    assert source.clone_command(destination)[-1] == str(destination)
    assert source.verify_command(destination)[-2:] == ("rev-parse", "HEAD")


def test_build_plan_uses_debug_build_and_custom_regression_test() -> None:
    checkout = Path("/tmp/cpython-relay")
    steps = build_plan(checkout, jobs=4)

    assert steps[0].argv == ("./configure", "--with-pydebug")
    assert steps[1].argv == ("make", "-j4")
    assert steps[2].argv[-1] == "test_relay_opcode"
    assert steps[3].argv[-4:] == (
        "test_builtin",
        "test_compile",
        "test_dis",
        "test_importlib.test_util",
    )
    with pytest.raises(ValueError, match="positive"):
        build_plan(checkout, jobs=0)


def test_patch_contract_requires_compiler_evaluator_api_and_test() -> None:
    patch = (
        Path(__file__).parents[1] / "artifacts" / "cpython-3.14.7-relay-opcode.patch"
    ).read_text(encoding="utf-8")

    assert "RELAY_TASK_ID" in verify_patch_contract(patch)
    with pytest.raises(ValueError, match="missing required markers"):
        verify_patch_contract("Python/bytecodes.c RELAY_TASK_ID")

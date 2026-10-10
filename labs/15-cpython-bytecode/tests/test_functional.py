"""Functional tests for selecting relay jobs and reviewing the CPython build.

These are functional tests. They drive the public package interface of
``lab_15_cpython_bytecode`` the way an operator would: pass query text to
``select_tasks`` over a list of jobs as their states change, and prepare the
optional interpreter build by checking the committed patch artifact and the
build plan. Nothing is cloned, compiled or executed.
"""

# pyright: strict

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

import lab_15_cpython_bytecode as lab

PATCH = Path(__file__).parents[1] / "artifacts" / "cpython-3.14.7-relay-opcode.patch"


@dataclass(frozen=True, slots=True)
class Job:
    task_id: str
    action: str
    state: str
    priority: int
    attempts: int


def selected_ids(jobs: list[Job], query: str) -> list[str]:
    return [job.task_id for job in lab.select_tasks(jobs, query)]


def test_query_follows_jobs_as_they_move_from_queued_to_succeeded_or_failed() -> None:
    jobs = [
        Job("task-17", "index /srv/a", "queued", 9, 0),
        Job("task-18", "archive /srv/b", "queued", 3, 0),
    ]
    query = 'state = queued and action contains "index"'
    assert selected_ids(jobs, query) == ["task-17"]

    jobs = [replace(job, state="running", attempts=1) for job in jobs]
    assert selected_ids(jobs, query) == []
    assert selected_ids(jobs, "state = running and attempts >= 1") == ["task-17", "task-18"]

    jobs = [replace(jobs[0], state="succeeded"), replace(jobs[1], state="failed")]
    assert selected_ids(jobs, "state = succeeded or state = failed") == ["task-17", "task-18"]
    assert selected_ids(jobs, "not state = failed") == ["task-17"]


def test_query_with_trailing_text_is_rejected_with_its_column() -> None:
    jobs = [Job("task-17", "index /srv/a", "queued", 9, 0)]

    with pytest.raises(lab.QuerySyntaxError, match="column 15"):
        lab.select_tasks(jobs, "state = queued; __import__('os')")
    with pytest.raises(lab.QuerySyntaxError, match="invalid query"):
        lab.select_tasks(jobs, 'priority contains "9"')


def test_committed_patch_and_build_plan_are_ready_for_the_optional_build(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "cpython-relay"
    source = lab.CPythonSource()

    markers = lab.verify_patch_contract(PATCH.read_text(encoding="utf-8"))
    steps = lab.build_plan(checkout, jobs=2)

    assert "PYC_MAGIC_NUMBER 3628" in markers
    assert source.clone_command(checkout)[-2:] == (source.repository, str(checkout))
    assert [step.argv[0] for step in steps] == [
        "./configure",
        "make",
        str(checkout / "python"),
        str(checkout / "python"),
    ]
    assert not checkout.exists()
    with pytest.raises(ValueError, match="missing required markers"):
        lab.verify_patch_contract(PATCH.read_text(encoding="utf-8").replace("ADDOP", "ADD"))

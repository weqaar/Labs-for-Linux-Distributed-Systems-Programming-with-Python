"""Functional tests for the relay trees and graphs checkpoint.

These tests drive the two composed public interfaces of the package,
``RelayPlan`` for planning jobs and ``OSPFTopology`` for routing between
workers. Each test builds a complete plan or topology through public methods
and checks the answers a scheduler or router would read from it.
"""

from __future__ import annotations

import pytest

from lab_06_trees_and_graphs import OSPFTopology, RelayPlan, TaskSpec


def build_release_plan() -> tuple[RelayPlan, dict[str, tuple[str, ...]]]:
    dependencies: dict[str, tuple[str, ...]] = {
        "task-11": (),
        "task-12": (),
        "task-13": ("task-11",),
        "task-14": ("task-11", "task-12"),
        "task-15": ("task-13", "task-14"),
        "task-16": ("task-15",),
    }
    priorities = {
        "task-11": 1,
        "task-12": 3,
        "task-13": 7,
        "task-14": 7,
        "task-15": 2,
        "task-16": 9,
    }
    plan = RelayPlan()
    for task_id, after in dependencies.items():
        plan.add(TaskSpec(task_id, f"step-{task_id}", priorities[task_id]), after=after)
    return plan, dependencies


def test_job_plan_runs_dependencies_first_while_priority_order_ranks_all_jobs() -> None:
    plan, dependencies = build_release_plan()

    execution = plan.execution_order()
    ranking = plan.priority_order()

    assert sorted(execution) == sorted(dependencies)
    for task_id, after in dependencies.items():
        for dependency in after:
            assert execution.index(dependency) < execution.index(task_id)
    assert ranking == ("task-16", "task-13", "task-14", "task-12", "task-15", "task-11")
    assert ranking.index("task-16") < ranking.index("task-15")


def test_rejected_job_additions_leave_the_existing_plan_unchanged() -> None:
    plan, _ = build_release_plan()
    execution = plan.execution_order()
    ranking = plan.priority_order()

    with pytest.raises(ValueError, match="task already exists: task-13"):
        plan.add(TaskSpec("task-13", "rerun", 99))
    with pytest.raises(LookupError, match="task-99"):
        plan.add(TaskSpec("task-17", "notify", 5), after=("task-16", "task-99"))
    with pytest.raises(ValueError, match="task-<integer>"):
        plan.add(TaskSpec("17", "notify", 5))

    assert plan.execution_order() == execution
    assert plan.priority_order() == ranking


def test_routing_table_follows_cheapest_path_and_changes_when_a_link_cost_changes() -> None:
    topology = OSPFTopology()
    topology.add_link("R1", "R2", 10)
    topology.add_link("R1", "R3", 2)
    topology.add_link("R3", "R4", 2)
    topology.add_link("R4", "R2", 2)
    topology.add_link("R2", "R5", 1)

    before = {route.destination: route for route in topology.routing_table("R1")}
    topology.add_link("R3", "R4", 20)
    after = {route.destination: route for route in topology.routing_table("R1")}

    assert (before["R1"].next_hop, before["R1"].cost) == (None, 0)
    assert (before["R5"].next_hop, before["R5"].cost) == ("R3", 7)
    assert before["R5"].path == ("R1", "R3", "R4", "R2", "R5")
    assert (after["R5"].next_hop, after["R5"].cost) == ("R2", 11)
    assert after["R4"].path == ("R1", "R2", "R4")
    assert after["R4"].cost == 12


def test_routing_reports_unknown_and_unreachable_routers_as_lookup_errors() -> None:
    topology = OSPFTopology()
    topology.add_link("R1", "R2", 1)
    topology.add_link("R8", "R9", 1)

    destinations = {route.destination for route in topology.routing_table("R1")}
    tree = topology.shortest_path_tree("R1")

    assert destinations == {"R1", "R2"}
    with pytest.raises(LookupError, match="unreachable: R9"):
        tree.path_to("R9")
    with pytest.raises(LookupError, match="graph node not found: R7"):
        topology.routing_table("R7")

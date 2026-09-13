"""Tests for the object-oriented relay checkpoint."""

from __future__ import annotations

import inspect

import pytest

from lab_05_object_oriented_design import (
    HandlerMeta,
    InMemoryTaskRepository,
    Task,
    TaskAction,
    TaskRepository,
    TaskState,
    WorkerPolicy,
    __version__,
    build_default_service,
)


def task(task_id: str = "task-5", action: TaskAction = TaskAction.INDEX) -> Task:
    return Task(task_id, action, "documents")


def test_python_object_type_identity_and_public_attributes_are_visible() -> None:
    relay_task = task()

    assert isinstance(relay_task, object)
    assert type(relay_task) is Task
    assert type(Task) is type
    assert isinstance(Task, object)
    assert relay_task is relay_task
    assert id(relay_task) == id(relay_task)
    assert {"id", "action", "payload", "state", "is_terminal", "with_state"} <= set(dir(relay_task))


def test_instance_attribute_access_binds_self_to_the_class_function() -> None:
    service = build_default_service()
    bound_submit = service.submit

    assert bound_submit.__self__ is service
    assert bound_submit.__func__ is type(service).submit
    assert inspect.signature(type(service).submit).parameters["self"]
    assert "TaskService" in (base.__name__ for base in type(service).__mro__)


def test_special_methods_supply_the_standard_object_protocol() -> None:
    relay_task = task()

    assert repr(relay_task).startswith("Task(")
    assert relay_task == task()
    assert hash(relay_task) == hash(task())
    assert "__repr__" in dir(relay_task)
    assert "__eq__" in dir(relay_task)


def test_service_is_composed_through_the_repository_interface() -> None:
    repository = InMemoryTaskRepository()
    service = build_default_service(repository)

    submitted = service.submit(task())
    completed = service.run(submitted.id)

    assert isinstance(repository, TaskRepository)
    assert completed.state is TaskState.SUCCEEDED
    assert completed.is_terminal is True
    assert repository.get("task-5") == completed


def test_method_decorator_preserves_metadata_and_records_audit() -> None:
    service = build_default_service()

    service.submit(task())

    assert service.audit_events[0].operation == "submit"
    assert service.audit_events[0].task_id == "task-5"
    assert service.submit.__name__ == "submit"
    assert str(inspect.signature(service.submit)) == "(task: 'Task') -> 'Task'"


def test_metaclass_registers_polymorphic_handlers_and_reflection_describes_mro() -> None:
    service = build_default_service()

    assert set(HandlerMeta.registry()) == {TaskAction.INDEX, TaskAction.ARCHIVE}
    description = service.describe_handler(TaskAction.INDEX)
    assert description["class"] == "IndexHandler"
    assert "PrefixMixin -> Handler" in description["mro"]
    assert description["signature"].startswith("(task:")


def test_dataclass_slots_prevent_accidental_attributes() -> None:
    relay_task = task()

    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(relay_task, "unexpected", True)


def test_descriptor_enforces_worker_policy() -> None:
    policy = WorkerPolicy(5)
    assert policy.max_attempts == 5

    with pytest.raises(ValueError, match="positive integer"):
        policy.max_attempts = 0


def test_dictionary_summary_includes_absent_states() -> None:
    service = build_default_service()
    service.submit(task())

    counts = service.counts_by_state()

    assert counts == {
        TaskState.QUEUED: 1,
        TaskState.RUNNING: 0,
        TaskState.SUCCEEDED: 0,
        TaskState.FAILED: 0,
    }


def test_repository_rejects_aliasing_and_missing_tasks() -> None:
    repository = InMemoryTaskRepository([task()])

    with pytest.raises(ValueError, match="already exists"):
        repository.add(task())
    with pytest.raises(LookupError, match="not found"):
        repository.get("task-99")


def test_version_is_exposed() -> None:
    assert __version__

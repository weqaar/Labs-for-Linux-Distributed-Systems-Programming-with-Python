"""Tests for the in-memory ZeroMQ relay patterns."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lab_20_zeromq_patterns import (
    CelerySettings,
    CommandRequest,
    DealerClient,
    DealerRouterChannel,
    PipelineStoppedError,
    RelayWorkPipeline,
    ReqStyleClient,
    RequestWedgeError,
    StatusPublisher,
    TaskAction,
    TaskState,
    TaskStatusEvent,
    TaskSubmission,
    __version__,
    create_relay_celery,
    pubsub_round_trip,
    submit_background_task,
    task_delivery_policy,
)


def make_submission(task_id: str = "task-17") -> TaskSubmission:
    return TaskSubmission(
        id=task_id,
        action=TaskAction.INDEX,
        target="blob://relay/inbox/17",
        submitted_at=datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc),
    )


def test_pipeline_drains_after_stop_is_requested() -> None:
    pipeline = RelayWorkPipeline()
    first = pipeline.submit(make_submission("task-17"))
    second = pipeline.submit(make_submission("task-18"))

    pipeline.request_stop()
    work_one = pipeline.pull("worker-a")
    work_two = pipeline.pull("worker-b")
    finished_one = pipeline.acknowledge("worker-a", "task-17", TaskState.SUCCEEDED, "done")
    finished_two = pipeline.acknowledge("worker-b", "task-18", TaskState.SUCCEEDED, "done")

    assert first.state is TaskState.QUEUED
    assert second.sequence == 2
    assert work_one is not None and work_one.id == "task-17"
    assert work_two is not None and work_two.id == "task-18"
    assert finished_one.sequence == 5
    assert finished_two.sequence == 6
    assert pipeline.drained()
    with pytest.raises(PipelineStoppedError):
        pipeline.submit(make_submission("task-19"))


def test_slow_subscriber_drop_count_reaches_high_water_mark() -> None:
    publisher = StatusPublisher()
    subscriber = publisher.subscribe("metrics", high_water_mark=1)

    publisher.publish(TaskStatusEvent(1, "task-17", TaskAction.INDEX, TaskState.QUEUED, "queued"))
    publisher.publish(TaskStatusEvent(2, "task-17", TaskAction.INDEX, TaskState.RUNNING, "running"))
    publisher.publish(TaskStatusEvent(3, "task-17", TaskAction.INDEX, TaskState.SUCCEEDED, "done"))

    events = subscriber.drain()
    metrics = subscriber.metrics()

    assert [event.sequence for event in events] == [1]
    assert metrics.dropped_messages == 2
    assert metrics.last_sequence == 3


def test_late_subscriber_accounts_for_missed_messages() -> None:
    publisher = StatusPublisher()
    publisher.publish(TaskStatusEvent(1, "task-17", TaskAction.INDEX, TaskState.QUEUED, "queued"))
    publisher.publish(TaskStatusEvent(2, "task-17", TaskAction.INDEX, TaskState.RUNNING, "running"))

    late = publisher.subscribe("late", high_water_mark=2)
    publisher.publish(TaskStatusEvent(3, "task-17", TaskAction.INDEX, TaskState.SUCCEEDED, "done"))

    assert late.metrics().missed_before_subscribe == 2
    assert [event.sequence for event in late.drain()] == [3]


def test_req_style_client_wedges_after_a_lost_reply() -> None:
    router = DealerRouterChannel()
    req_client = ReqStyleClient(router)

    assert (
        req_client.send(
            CommandRequest("cli", "req-1", "task-17", "cancel"),
            lose_reply=True,
        )
        is None
    )
    with pytest.raises(RequestWedgeError, match="lost reply"):
        req_client.send(CommandRequest("cli", "req-2", "task-17", "status"))


def test_dealer_client_recovers_a_lost_reply_by_request_id() -> None:
    router = DealerRouterChannel()
    dealer = DealerClient(router)
    first = CommandRequest("cli", "req-1", "task-17", "cancel")
    second = CommandRequest("cli", "req-2", "task-17", "status")

    assert dealer.send(first, lose_reply=True) is None
    second_reply = dealer.send(second)
    recovered = dealer.recover(first)

    assert second_reply is not None and second_reply.request_id == "req-2"
    assert recovered.request_id == "req-1"
    assert recovered.message == "cancel accepted"
    assert not dealer.pending()


def test_real_pubsub_socket_waits_for_subscription_without_sleep() -> None:
    assert pubsub_round_trip(b"task.", b"task-17:running") == (
        b"task.",
        b"task-17:running",
    )


def test_celery_eager_worker_executes_the_same_serialized_task_contract() -> None:
    app = create_relay_celery(
        CelerySettings(broker_url="memory://", result_backend="cache+memory://"),
        eager=True,
    )
    task = app.tasks["relay.execute"]

    result = task.apply(
        kwargs={
            "payload": {
                "id": "task-17",
                "action": "index",
                "target": "blob://relay/inbox/17",
            }
        }
    )

    assert result.get(timeout=1) == {
        "id": "task-17",
        "action": "index",
        "target": "blob://relay/inbox/17",
        "state": "succeeded",
    }


def test_celery_delivery_policy_limits_prefetch_and_redelivers_worker_loss() -> None:
    app = create_relay_celery(
        CelerySettings(
            broker_url="memory://",
            result_backend="cache+memory://",
            visibility_timeout_seconds=900,
        ),
        eager=True,
    )

    assert task_delivery_policy(app) == {
        "acks_late": True,
        "reject_on_worker_lost": True,
        "prefetch_multiplier": 1,
        "visibility_timeout": 900,
        "route": "relay.tasks",
    }


def test_web_request_can_acknowledge_before_the_background_contract_completes() -> None:
    app = create_relay_celery(
        CelerySettings(broker_url="memory://", result_backend="cache+memory://"),
        eager=True,
    )

    accepted = submit_background_task(
        app,
        task_id="task-17",
        action="index",
        target="blob://relay/inbox/17",
    )

    assert accepted.task_id == "task-17"
    assert accepted.celery_id
    assert accepted.state == "queued"


def test_version_is_exposed() -> None:
    assert __version__

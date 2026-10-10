"""Functional tests for the ZeroMQ messaging checkpoint.

These tests drive the public package interface as a relay deployment would use
it: `RelayWorkPipeline` with a `StatusPublisher` for PUSH/PULL work and PUB/SUB
status, `DealerClient` with `DealerRouterChannel` for commands,
`pubsub_round_trip` for a real in-process ZeroMQ socket pair, and
`submit_background_task` on an eager Celery app. Each test follows one job or
command from submission to the result a client observes.
"""

# pyright: strict

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
    StatusPublisher,
    TaskAction,
    TaskState,
    TaskSubmission,
    UnknownTaskError,
    create_relay_celery,
    pubsub_round_trip,
    submit_background_task,
)


def make_submission(task_id: str) -> TaskSubmission:
    return TaskSubmission(
        id=task_id,
        action=TaskAction.INDEX,
        target=f"blob://relay/inbox/{task_id}",
        submitted_at=datetime(2026, 8, 18, 5, 52, 44, tzinfo=timezone.utc),
    )


def test_submitted_job_runs_to_succeeded_and_a_subscriber_sees_every_state() -> None:
    publisher = StatusPublisher()
    watcher = publisher.subscribe("relayctl-watch", high_water_mark=8)
    pipeline = RelayWorkPipeline(publisher)

    pipeline.submit(make_submission("task-17"))
    work = pipeline.pull("worker-a")
    if work is None:
        pytest.fail("worker-a received no work")
    pipeline.acknowledge("worker-a", work.id, TaskState.SUCCEEDED, "indexed")
    pipeline.request_stop()

    events = watcher.drain()
    assert [(event.id, event.state) for event in events] == [
        ("task-17", TaskState.QUEUED),
        ("task-17", TaskState.RUNNING),
        ("task-17", TaskState.SUCCEEDED),
    ]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert watcher.metrics().dropped_messages == 0
    assert pipeline.drained()


def test_failed_job_and_misrouted_acknowledgements_are_reported() -> None:
    publisher = StatusPublisher()
    watcher = publisher.subscribe("relayctl-watch", high_water_mark=8)
    pipeline = RelayWorkPipeline(publisher)
    pipeline.submit(make_submission("task-17"))
    pipeline.pull("worker-a")

    with pytest.raises(ValueError, match="owned by worker-a"):
        pipeline.acknowledge("worker-b", "task-17", TaskState.SUCCEEDED, "stolen")
    with pytest.raises(UnknownTaskError):
        pipeline.acknowledge("worker-a", "task-404", TaskState.SUCCEEDED, "unknown")
    failed = pipeline.acknowledge("worker-a", "task-17", TaskState.FAILED, "target missing")
    pipeline.request_stop()

    assert failed.state is TaskState.FAILED
    assert [event.state for event in watcher.drain()] == [
        TaskState.QUEUED,
        TaskState.RUNNING,
        TaskState.FAILED,
    ]
    assert pipeline.drained()
    with pytest.raises(PipelineStoppedError):
        pipeline.submit(make_submission("task-18"))


def test_lost_command_reply_is_recovered_without_a_second_effect() -> None:
    router = DealerRouterChannel()
    dealer = DealerClient(router)
    cancel = CommandRequest("relayctl", "req-1", "task-17", "cancel")

    lost = dealer.send(cancel, lose_reply=True)
    pending_after_loss = dealer.pending()
    recovered = dealer.recover(cancel)
    replayed = router.handle(cancel)

    assert lost is None
    assert pending_after_loss == frozenset({"req-1"})
    assert recovered.task_id == "task-17"
    assert recovered.accepted
    assert recovered.message == "cancel accepted"
    assert replayed is recovered
    assert dealer.pending() == frozenset()


def test_status_event_crosses_a_real_zeromq_socket_and_empty_topic_is_refused() -> None:
    topic, payload = pubsub_round_trip(b"task-17.", b"running", timeout_ms=2_000)

    assert (topic, payload) == (b"task-17.", b"running")
    with pytest.raises(ValueError, match="topic must not be empty"):
        pubsub_round_trip(b"", b"running", timeout_ms=2_000)


def test_background_job_is_accepted_and_a_malformed_id_is_rejected() -> None:
    app = create_relay_celery(
        CelerySettings(broker_url="memory://", result_backend="cache+memory://"),
        eager=True,
    )
    try:
        accepted = submit_background_task(
            app, task_id="task-17", action="index", target="blob://relay/inbox/17"
        )
        with pytest.raises(ValueError, match="task-<positive integer>"):
            submit_background_task(
                app, task_id="job-17", action="index", target="blob://relay/inbox/17"
            )
    finally:
        app.close()

    assert accepted.task_id == "task-17"
    assert accepted.state == "queued"
    assert accepted.celery_id

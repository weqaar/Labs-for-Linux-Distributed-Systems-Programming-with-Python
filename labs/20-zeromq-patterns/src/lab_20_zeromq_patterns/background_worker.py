"""Celery background-worker boundary for relay web requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from celery import Celery


@dataclass(frozen=True, slots=True)
class CelerySettings:
    """Broker, result, and delivery policy for relay workers."""

    broker_url: str = "redis://valkey:6379/0"
    result_backend: str = "redis://valkey:6379/1"
    visibility_timeout_seconds: int = 3_600

    def __post_init__(self) -> None:
        if self.visibility_timeout_seconds < 1:
            raise ValueError("visibility_timeout_seconds must be positive")


def create_relay_celery(
    settings: CelerySettings = CelerySettings(),
    *,
    eager: bool = False,
) -> Celery:
    """Create the relay worker app with explicit at-least-once delivery policy."""

    app = Celery("relay", broker=settings.broker_url, backend=settings.result_backend)
    app.conf.update(
        accept_content=("json",),
        task_serializer="json",
        result_serializer="json",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={
            "visibility_timeout": settings.visibility_timeout_seconds,
        },
        task_routes={"relay.execute": {"queue": "relay.tasks"}},
        task_always_eager=eager,
        task_store_eager_result=eager,
        task_eager_propagates=eager,
    )

    @app.task(
        name="relay.execute",
        autoretry_for=(RetryableTaskError,),
        retry_backoff=True,
        retry_jitter=True,
        max_retries=5,
    )
    def execute(payload: dict[str, str]) -> dict[str, str]:
        task_id = payload.get("id", "")
        action = payload.get("action", "")
        target = payload.get("target", "")
        if not task_id.startswith("task-") or not task_id[5:].isdigit():
            raise ValueError("id must match task-<positive integer>")
        if not action or not target:
            raise ValueError("action and target must not be empty")
        return {
            "id": task_id,
            "action": action,
            "target": target,
            "state": "succeeded",
        }

    return app


class RetryableTaskError(RuntimeError):
    """A dependency failure for which Celery may schedule a bounded retry."""


@dataclass(frozen=True, slots=True)
class AcceptedBackgroundTask:
    """HTTP-facing acknowledgement returned before a worker runs."""

    task_id: str
    celery_id: str
    state: str = "queued"


def submit_background_task(
    app: Celery,
    *,
    task_id: str,
    action: str,
    target: str,
) -> AcceptedBackgroundTask:
    """Queue a relay task and return a 202-style acknowledgement."""

    result = app.tasks["relay.execute"].apply_async(
        kwargs={"payload": {"id": task_id, "action": action, "target": target}},
        queue="relay.tasks",
    )
    return AcceptedBackgroundTask(task_id=task_id, celery_id=str(result.id))


def task_delivery_policy(app: Celery) -> dict[str, Any]:
    """Expose settings that must be reviewed together for at-least-once work."""

    return {
        "acks_late": app.conf.task_acks_late,
        "reject_on_worker_lost": app.conf.task_reject_on_worker_lost,
        "prefetch_multiplier": app.conf.worker_prefetch_multiplier,
        "visibility_timeout": app.conf.broker_transport_options["visibility_timeout"],
        "route": app.conf.task_routes["relay.execute"]["queue"],
    }

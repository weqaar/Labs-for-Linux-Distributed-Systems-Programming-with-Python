"""Airflow-free relay DAG specification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


@dataclass(frozen=True)
class RetrySpec:
    """Retry settings compatible with an Airflow adapter."""

    retries: int
    delay: timedelta


@dataclass(frozen=True)
class ResourceSpec:
    """Pool and resource semantics for one task."""

    pool: str
    slots: int = 1
    max_active_tis_per_dag: int | None = None
    cpu: float | None = None
    memory_mb: int | None = None


@dataclass(frozen=True)
class DagTaskSpec:
    """Declarative task spec."""

    task_id: str
    title: str
    kind: str
    upstream: tuple[str, ...] = ()
    deferrable: bool = False
    dynamic_map_from: str | None = None
    callable_name: str | None = None
    retry: RetrySpec = field(
        default_factory=lambda: RetrySpec(retries=2, delay=timedelta(minutes=5))
    )
    resources: ResourceSpec | None = None


@dataclass(frozen=True)
class UtcDailySchedule:
    """Daily UTC schedule with bounded catchup."""

    start_at: datetime
    hour: int
    minute: int = 0
    catchup: bool = True
    max_active_runs: int = 2
    max_backfill_runs: int = 7

    def __post_init__(self) -> None:
        if self.start_at.tzinfo != UTC:
            raise ValueError("start_at must be timezone.utc aware")
        if not 0 <= self.hour <= 23:
            raise ValueError("hour must be in [0, 23]")
        if not 0 <= self.minute <= 59:
            raise ValueError("minute must be in [0, 59]")
        aligned = self.start_at.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if self.start_at != aligned:
            raise ValueError("start_at must align with the scheduled minute")

    @property
    def cron(self) -> str:
        return f"{self.minute} {self.hour} * * *"

    def bounded_backfill(
        self,
        *,
        earliest: datetime,
        latest: datetime,
    ) -> tuple[datetime, ...]:
        if earliest.tzinfo != UTC or latest.tzinfo != UTC:
            raise ValueError("earliest and latest must be timezone.utc aware")
        if latest < earliest:
            return ()
        logical_dates = self._logical_dates_between(earliest=earliest, latest=latest)
        if not self.catchup:
            return logical_dates[-1:] if logical_dates else ()
        if len(logical_dates) <= self.max_backfill_runs:
            return logical_dates
        return logical_dates[-self.max_backfill_runs :]

    def _logical_dates_between(
        self,
        *,
        earliest: datetime,
        latest: datetime,
    ) -> tuple[datetime, ...]:
        first = (
            self.start_at
            if self.start_at >= earliest
            else _floor_to_schedule(
                latest=earliest,
                hour=self.hour,
                minute=self.minute,
            )
        )
        if first < self.start_at:
            first = self.start_at
        current = first
        dates: list[datetime] = []
        while current <= latest:
            dates.append(current)
            current += timedelta(days=1)
        return tuple(dates)


@dataclass(frozen=True)
class DagDefinition:
    """DAG definition that can be adapted to Airflow later."""

    dag_id: str
    schedule: UtcDailySchedule
    tasks: tuple[DagTaskSpec, ...]

    def task(self, task_id: str) -> DagTaskSpec:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def as_airflow_blueprint(self) -> dict[str, object]:
        """Return a serialisable blueprint for an optional Airflow adapter."""
        return {
            "dag_id": self.dag_id,
            "schedule": self.schedule.cron,
            "timezone": "UTC",
            "catchup": self.schedule.catchup,
            "max_active_runs": self.schedule.max_active_runs,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "kind": task.kind,
                    "upstream": list(task.upstream),
                    "deferrable": task.deferrable,
                    "dynamic_map_from": task.dynamic_map_from,
                    "callable_name": task.callable_name,
                    "retries": task.retry.retries,
                    "retry_delay_seconds": int(task.retry.delay.total_seconds()),
                    "resources": None
                    if task.resources is None
                    else {
                        "pool": task.resources.pool,
                        "slots": task.resources.slots,
                        "max_active_tis_per_dag": task.resources.max_active_tis_per_dag,
                        "cpu": task.resources.cpu,
                        "memory_mb": task.resources.memory_mb,
                    },
                }
                for task in self.tasks
            ],
        }


def build_relay_dag() -> DagDefinition:
    """Build the relay DAG without touching the network or creating clients."""
    schedule = UtcDailySchedule(
        start_at=datetime(2026, 1, 1, 2, 0, tzinfo=UTC),
        hour=2,
        minute=0,
        catchup=True,
        max_active_runs=2,
        max_backfill_runs=7,
    )
    tasks = (
        DagTaskSpec(
            task_id="wait-for-partition-snapshot",
            title="Wait for the upstream partition snapshot",
            kind="sensor",
            deferrable=True,
            callable_name="lab_26_airflow_dags.runtime.wait_for_partition_snapshot",
            resources=ResourceSpec(pool="artifact-waits", slots=1, max_active_tis_per_dag=1),
        ),
        DagTaskSpec(
            task_id="discover-pending-tasks",
            title="Discover pending relay tasks",
            kind="python",
            upstream=("wait-for-partition-snapshot",),
            callable_name="lab_26_airflow_dags.runtime.discover_pending_tasks",
        ),
        DagTaskSpec(
            task_id="hydrate-task-context",
            title="Hydrate relay task context",
            kind="python",
            upstream=("discover-pending-tasks",),
            dynamic_map_from="discover-pending-tasks",
            callable_name="lab_26_airflow_dags.runtime.hydrate_task_context",
        ),
        DagTaskSpec(
            task_id="run-relay-task",
            title="Run relay task handler",
            kind="python",
            upstream=("hydrate-task-context",),
            dynamic_map_from="discover-pending-tasks",
            callable_name="lab_26_airflow_dags.runtime.run_relay_task",
            resources=ResourceSpec(
                pool="relay-workers",
                slots=1,
                max_active_tis_per_dag=4,
                cpu=0.5,
                memory_mb=512,
            ),
        ),
        DagTaskSpec(
            task_id="persist-task-status",
            title="Persist relay task status",
            kind="python",
            upstream=("run-relay-task",),
            callable_name="lab_26_airflow_dags.runtime.persist_task_status",
        ),
        DagTaskSpec(
            task_id="publish-run-metrics",
            title="Publish relay run metrics",
            kind="python",
            upstream=("persist-task-status",),
            callable_name="lab_26_airflow_dags.runtime.publish_run_metrics",
        ),
    )
    return DagDefinition(dag_id="relay-daily", schedule=schedule, tasks=tasks)


def _floor_to_schedule(*, latest: datetime, hour: int, minute: int) -> datetime:
    candidate = latest.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > latest:
        candidate -= timedelta(days=1)
    return candidate


RELAY_DAG = build_relay_dag()

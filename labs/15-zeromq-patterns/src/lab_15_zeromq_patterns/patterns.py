"""In-memory models of the relay ZeroMQ patterns."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from lab_15_zeromq_patterns.contract import (
    CommandReply,
    CommandRequest,
    TaskState,
    TaskStatusEvent,
    TaskSubmission,
)


class PipelineStoppedError(RuntimeError):
    """Raised when new work arrives after shutdown started."""


class UnknownTaskError(KeyError):
    """Raised when a task ID is not known to the work pipeline."""


class RequestWedgeError(RuntimeError):
    """Raised when a REQ-style client is still waiting on a lost reply."""


@dataclass(frozen=True)
class SubscriberMetrics:
    """What to monitor for one PUB/SUB subscriber."""

    subscriber_id: str
    high_water_mark: int
    queued_messages: int
    dropped_messages: int
    missed_before_subscribe: int
    last_sequence: int


class StatusSubscription:
    """Per-subscriber queue with high-water accounting."""

    def __init__(
        self, subscriber_id: str, *, high_water_mark: int, missed_before_subscribe: int
    ) -> None:
        if high_water_mark < 1:
            raise ValueError("high_water_mark must be at least one")
        self._subscriber_id = subscriber_id
        self._high_water_mark = high_water_mark
        self._missed_before_subscribe = missed_before_subscribe
        self._dropped_messages = 0
        self._last_sequence = 0
        self._queue: deque[TaskStatusEvent] = deque()

    def offer(self, event: TaskStatusEvent) -> None:
        self._last_sequence = event.sequence
        if len(self._queue) >= self._high_water_mark:
            self._dropped_messages += 1
            return
        self._queue.append(event)

    def drain(self) -> tuple[TaskStatusEvent, ...]:
        drained = tuple(self._queue)
        self._queue.clear()
        return drained

    def metrics(self) -> SubscriberMetrics:
        return SubscriberMetrics(
            subscriber_id=self._subscriber_id,
            high_water_mark=self._high_water_mark,
            queued_messages=len(self._queue),
            dropped_messages=self._dropped_messages,
            missed_before_subscribe=self._missed_before_subscribe,
            last_sequence=self._last_sequence,
        )


class StatusPublisher:
    """PUB/SUB fan-out with explicit loss counters."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, StatusSubscription] = {}
        self._last_sequence = 0

    def subscribe(self, subscriber_id: str, *, high_water_mark: int) -> StatusSubscription:
        subscription = StatusSubscription(
            subscriber_id,
            high_water_mark=high_water_mark,
            missed_before_subscribe=self._last_sequence,
        )
        self._subscriptions[subscriber_id] = subscription
        return subscription

    def publish(self, event: TaskStatusEvent) -> None:
        if event.sequence <= self._last_sequence:
            raise ValueError("event sequences must increase")
        self._last_sequence = event.sequence
        for subscription in self._subscriptions.values():
            subscription.offer(event)


class RelayWorkPipeline:
    """Relay work queue that models PUSH/PULL and graceful drain."""

    def __init__(self, publisher: StatusPublisher | None = None) -> None:
        self._publisher = publisher or StatusPublisher()
        self._queue: deque[TaskSubmission] = deque()
        self._known_tasks: dict[str, TaskSubmission] = {}
        self._inflight_workers: dict[str, str] = {}
        self._next_sequence = 0
        self._stopping = False

    @property
    def publisher(self) -> StatusPublisher:
        return self._publisher

    def submit(self, submission: TaskSubmission) -> TaskStatusEvent:
        if self._stopping:
            raise PipelineStoppedError("pipeline is draining and not accepting new tasks")
        self._queue.append(submission)
        self._known_tasks[submission.id] = submission
        return self._publish(submission, TaskState.QUEUED, "queued for work")

    def pull(self, worker_id: str) -> TaskSubmission | None:
        if self._queue:
            submission = self._queue.popleft()
            self._inflight_workers[submission.id] = worker_id
            self._publish(submission, TaskState.RUNNING, f"worker {worker_id} accepted task")
            return submission
        return None

    def acknowledge(
        self, worker_id: str, task_id: str, state: TaskState, detail: str
    ) -> TaskStatusEvent:
        try:
            current_worker = self._inflight_workers[task_id]
            submission = self._known_tasks[task_id]
        except KeyError as exc:
            raise UnknownTaskError(task_id) from exc
        if current_worker != worker_id:
            raise ValueError(f"task {task_id} is owned by {current_worker}")
        del self._inflight_workers[task_id]
        return self._publish(submission, state, detail)

    def request_stop(self) -> None:
        self._stopping = True

    def drained(self) -> bool:
        return self._stopping and not self._queue and not self._inflight_workers

    def _publish(
        self, submission: TaskSubmission, state: TaskState, detail: str
    ) -> TaskStatusEvent:
        self._next_sequence += 1
        event = TaskStatusEvent(
            sequence=self._next_sequence,
            id=submission.id,
            action=submission.action,
            state=state,
            detail=detail,
        )
        self._publisher.publish(event)
        return event


class DealerRouterChannel:
    """ROUTER side with reply cache for recoverable request IDs."""

    def __init__(self) -> None:
        self._reply_cache: dict[tuple[str, str], CommandReply] = {}

    def handle(self, request: CommandRequest) -> CommandReply:
        key = (request.client_id, request.request_id)
        cached = self._reply_cache.get(key)
        if cached is not None:
            return cached
        reply = CommandReply(
            client_id=request.client_id,
            request_id=request.request_id,
            task_id=request.task_id,
            accepted=True,
            message=f"{request.command} accepted",
        )
        self._reply_cache[key] = reply
        return reply


class ReqStyleClient:
    """REQ-style client that wedges after a lost reply."""

    def __init__(self, router: DealerRouterChannel) -> None:
        self._router = router
        self._waiting_for: str | None = None

    def send(self, request: CommandRequest, *, lose_reply: bool = False) -> CommandReply | None:
        if self._waiting_for is not None:
            raise RequestWedgeError("REQ socket is still waiting for the lost reply")
        self._waiting_for = request.request_id
        reply = self._router.handle(request)
        if lose_reply:
            return None
        self._waiting_for = None
        return reply


class DealerClient:
    """DEALER-style client that can recover a lost reply by request ID."""

    def __init__(self, router: DealerRouterChannel) -> None:
        self._router = router
        self._pending: set[str] = set()

    def send(self, request: CommandRequest, *, lose_reply: bool = False) -> CommandReply | None:
        self._pending.add(request.request_id)
        reply = self._router.handle(request)
        if lose_reply:
            return None
        self._pending.discard(request.request_id)
        return reply

    def recover(self, request: CommandRequest) -> CommandReply:
        reply = self._router.handle(request)
        self._pending.discard(request.request_id)
        return reply

    def pending(self) -> frozenset[str]:
        return frozenset(self._pending)

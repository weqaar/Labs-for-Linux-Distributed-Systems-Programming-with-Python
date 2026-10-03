# SPDX-License-Identifier: Apache-2.0
"""Bounded, process-local job notifications without a replay guarantee."""

from __future__ import annotations

from dataclasses import dataclass, field
from queue import Full, Queue
from threading import Event


@dataclass(frozen=True)
class JobEvent:
    """A snapshot or change, ordered within one service process."""

    sequence: int
    job: dict[str, object]


@dataclass(eq=False)
class JobSubscription:
    """One bounded mailbox; overflow requires a new snapshot, not silent loss."""

    task_id: str
    events: Queue[JobEvent] = field(default_factory=lambda: Queue(maxsize=32))
    overflow: Event = field(default_factory=Event)

    def publish(self, event: JobEvent) -> None:
        """Enqueue without blocking a job transition on a slow observer."""
        if self.overflow.is_set():
            return
        try:
            self.events.put_nowait(event)
        except Full:
            self.overflow.set()

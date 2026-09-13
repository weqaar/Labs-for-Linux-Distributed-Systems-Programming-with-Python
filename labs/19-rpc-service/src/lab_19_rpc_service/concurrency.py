"""The blocking-work trap behind an ASGI event loop, made observable.

An ``async def`` route runs directly on the worker's event loop. A call that
blocks the thread instead of awaiting, such as ``time.sleep`` or a synchronous
socket read, stalls every other coroutine scheduled on that loop, not only the
request that made the call. The two functions below give tests something
concrete to assert on: one blocks the loop, the other offloads the same work
to a thread and lets the loop keep scheduling.
"""

from __future__ import annotations

import asyncio
import time


def blocking_write(duration_s: float) -> None:
    """Simulate blocking I/O called directly from an async route.

    This is the trap: calling this from inside ``async def`` stalls the whole
    worker for ``duration_s`` because nothing yields control back to the
    loop.
    """

    if duration_s < 0:
        raise ValueError("duration_s must not be negative")
    time.sleep(duration_s)


async def offloaded_write(duration_s: float) -> None:
    """Run the same blocking work on a thread instead of the event loop.

    ``asyncio.to_thread`` hands the call to the default executor and awaits
    it, so the event loop is free to run other coroutines while it waits.
    """

    if duration_s < 0:
        raise ValueError("duration_s must not be negative")
    await asyncio.to_thread(time.sleep, duration_s)


class Heartbeat:
    """A tick counter driven by a background coroutine on the same loop.

    A heartbeat that keeps advancing proves the event loop kept scheduling
    other work while a handler ran; one that stalls proves it did not.
    """

    def __init__(self, *, interval_s: float) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self._interval_s = interval_s
        self.ticks = 0
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Heartbeat:
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._task is not None
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            self.ticks += 1

"""Tests for the blocking-work trap described in the chapter.

Both tests run the same simulated I/O for the same duration. The only
difference is whether it is awaited through a thread or called inline inside
a coroutine. A heartbeat coroutine on the same event loop proves the
difference: it keeps ticking when the work is offloaded, and it stalls when
the work blocks the loop directly.
"""

from __future__ import annotations

import asyncio

import pytest

from lab_19_rpc_service import Heartbeat, blocking_write, offloaded_write


def test_offloaded_write_lets_the_heartbeat_keep_ticking() -> None:
    async def scenario() -> int:
        async with Heartbeat(interval_s=0.02) as heartbeat:
            await offloaded_write(0.15)
            return heartbeat.ticks

    ticks = asyncio.run(scenario())

    assert ticks >= 3


def test_blocking_write_stalls_the_event_loop() -> None:
    async def scenario() -> int:
        async with Heartbeat(interval_s=0.02) as heartbeat:
            # blocking_write is synchronous. Calling it directly inside a
            # coroutine, instead of awaiting an offloaded version, is exactly
            # the trap: nothing yields back to the loop while it runs.
            blocking_write(0.15)
            return heartbeat.ticks

    ticks = asyncio.run(scenario())

    assert ticks == 0


def test_blocking_write_and_offloaded_write_reject_a_negative_duration() -> None:
    with pytest.raises(ValueError, match="duration_s"):
        blocking_write(-1)

    async def scenario() -> None:
        await offloaded_write(-1)

    with pytest.raises(ValueError, match="duration_s"):
        asyncio.run(scenario())


def test_heartbeat_rejects_a_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="interval_s"):
        Heartbeat(interval_s=0)

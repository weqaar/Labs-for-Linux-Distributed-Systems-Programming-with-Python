"""REST client with one bounded retry policy."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


class RelayClientError(RuntimeError):
    """The REST service could not provide a usable answer."""


class TaskNotFound(RelayClientError):
    """The requested task does not exist."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bound retry attempts and delays."""

    attempts: int = 3
    base_delay: float = 0.25
    max_delay: float = 2.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least one")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("retry delays must not be negative")

    def delay(self, attempt: int, jitter: Callable[[float, float], float]) -> float:
        """Return capped exponential backoff plus full jitter."""
        backoff = min(self.base_delay * 2**attempt, self.max_delay)
        return backoff + jitter(0, backoff)


class RelayClient:
    """HTTP boundary used by relayctl."""

    RETRYABLE = frozenset({429, 502, 503, 504})

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        retry: RetryPolicy = RetryPolicy(),
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._retry = retry
        self._sleep = sleep
        self._jitter = jitter
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(10.0, connect=3.0),
            transport=transport,
        )

    def task(self, task_id: str) -> dict[str, Any]:
        """Fetch one task, retrying only an idempotent GET."""
        for attempt in range(self._retry.attempts):
            try:
                response = self._http.get(f"/tasks/{task_id}")
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt == self._retry.attempts - 1:
                    raise RelayClientError("service unreachable after retry budget") from exc
                self._wait(attempt)
                continue

            if response.status_code == 404:
                raise TaskNotFound(f"task not found: {task_id}")
            if response.status_code in self.RETRYABLE:
                if attempt == self._retry.attempts - 1:
                    raise RelayClientError(
                        f"service returned HTTP {response.status_code} after retry budget"
                    )
                self._wait(attempt)
                continue

            try:
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RelayClientError(f"invalid service response: {exc}") from exc
            if not isinstance(payload, dict):
                raise RelayClientError("invalid service response: expected a JSON object")
            return payload

        raise AssertionError("retry loop exhausted without returning")

    def _wait(self, attempt: int) -> None:
        self._sleep(self._retry.delay(attempt, self._jitter))

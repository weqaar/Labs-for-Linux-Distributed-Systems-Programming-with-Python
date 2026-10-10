"""Functional tests for reconciling on-prem resources through Harbor.

These tests drive the public ``Reconciler`` composed with the real
``HarborAdapter``. A stateful fake Harbor v2 API behind ``httpx.MockTransport``
stands in for the registry, so the full plan, apply and replan cycle runs
without a network or credentials.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from lab_37_on_prem_cloud_apis import (
    Action,
    DesiredResource,
    HarborAdapter,
    RateLimited,
    Reconciler,
)

BASE_URL = "https://registry.example"
PROJECTS = "/api/v2.0/projects"


class FakeHarbor:
    """In-memory Harbor project API with scripted write failures."""

    def __init__(self) -> None:
        self.projects: dict[str, dict[str, object]] = {}
        self.writes: list[tuple[str, str]] = []
        self.rate_limit_posts = False
        self.lose_next_post_reply = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == PROJECTS:
            return httpx.Response(
                200,
                json=list(self.projects.values()),
                headers={"X-Total-Count": str(len(self.projects))},
            )
        if request.method == "GET":
            project = self.projects.get(path.rsplit("/", 1)[-1])
            return httpx.Response(404) if project is None else httpx.Response(200, json=project)
        if request.method == "POST" and self.rate_limit_posts:
            return httpx.Response(429, headers={"Retry-After": "3"})
        body: dict[str, object] = json.loads(request.content)
        self.writes.append((request.method, request.headers["Idempotency-Key"]))
        if request.method == "POST":
            name = str(body.pop("project_name"))
            self.projects[name] = {"name": name, **body}
            if self.lose_next_post_reply:
                self.lose_next_post_reply = False
                raise httpx.ReadError("reply lost after Harbor stored the project")
            return httpx.Response(201)
        name = path.rsplit("/", 1)[-1]
        self.projects[name] = {"name": name, **body}
        return httpx.Response(200)


@pytest.fixture
def harbor() -> FakeHarbor:
    return FakeHarbor()


@pytest.fixture
def adapter(harbor: FakeHarbor) -> Iterator[HarborAdapter]:
    instance = HarborAdapter(BASE_URL, httpx.Client(transport=httpx.MockTransport(harbor.handle)))
    try:
        yield instance
    finally:
        instance.close()


def relay_project(public: bool) -> DesiredResource:
    return DesiredResource("harbor.project", "relay", {"public": public})


def test_missing_project_is_created_once_and_then_planned_as_noop(
    harbor: FakeHarbor, adapter: HarborAdapter
) -> None:
    reconciler = Reconciler({adapter.kind: adapter}, sleep=lambda _: None)
    desired = relay_project(public=False)

    first_plan = reconciler.plan([desired])
    applied = reconciler.apply(first_plan, timeout=10)
    second_plan = reconciler.plan([desired])

    assert [change.action for change in first_plan] == [Action.CREATE]
    assert applied[0].name == "relay"
    assert [change.action for change in second_plan] == [Action.NOOP]
    assert harbor.writes == [("POST", desired.idempotency_key)]
    assert [state.name for state in reconciler.list_all(adapter.kind, page_size=1)] == ["relay"]


def test_changed_setting_is_planned_as_update_and_converges(
    harbor: FakeHarbor, adapter: HarborAdapter
) -> None:
    reconciler = Reconciler({adapter.kind: adapter}, sleep=lambda _: None)
    reconciler.apply(reconciler.plan([relay_project(public=False)]), timeout=10)
    changed = relay_project(public=True)

    plan = reconciler.plan([changed])
    reconciler.apply(plan, timeout=10)

    assert [change.action for change in plan] == [Action.UPDATE]
    assert harbor.projects["relay"]["public"] is True
    assert [method for method, _ in harbor.writes] == ["POST", "PUT"]
    assert [change.action for change in reconciler.plan([changed])] == [Action.NOOP]


def test_lost_create_reply_is_retried_without_creating_a_duplicate(
    harbor: FakeHarbor, adapter: HarborAdapter
) -> None:
    sleeps: list[float] = []
    reconciler = Reconciler({adapter.kind: adapter}, sleep=sleeps.append)
    desired = relay_project(public=False)
    harbor.lose_next_post_reply = True

    reconciler.apply(reconciler.plan([desired]), timeout=10)

    assert sleeps == [1.0]
    assert list(harbor.projects) == ["relay"]
    assert harbor.writes == [
        ("POST", desired.idempotency_key),
        ("PUT", desired.idempotency_key),
    ]


def test_persistent_rate_limit_stops_at_the_attempt_budget_without_a_write(
    harbor: FakeHarbor, adapter: HarborAdapter
) -> None:
    sleeps: list[float] = []
    reconciler = Reconciler({adapter.kind: adapter}, attempts=3, sleep=sleeps.append)
    harbor.rate_limit_posts = True

    with pytest.raises(RateLimited) as raised:
        reconciler.apply(reconciler.plan([relay_project(public=False)]), timeout=10)

    assert raised.value.retry_after == 3.0
    assert sleeps == [3.0, 3.0]
    assert harbor.projects == {}
    assert harbor.writes == []

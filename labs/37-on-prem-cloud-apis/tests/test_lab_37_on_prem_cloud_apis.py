"""Offline tests for reconciliation policy and concrete adapter translation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import requests
from keystoneauth1 import exceptions as keystone_exceptions
from kubernetes import client
from openstack import exceptions as openstack_exceptions

from lab_37_on_prem_cloud_apis import (
    Action,
    DeadlineExceeded,
    DesiredResource,
    FakeAdapter,
    HarborAdapter,
    KubernetesAdapter,
    OpenStackAdapter,
    PlannedChange,
    RateLimited,
    Reconciler,
    RedfishClient,
    RedfishProtocolError,
    RequestLost,
    ResourceConflict,
    ResourceState,
    ResponseLost,
    ServiceUnavailable,
    UnknownOutcome,
)
from lab_37_on_prem_cloud_apis.adapters import _translate
from lab_37_on_prem_cloud_apis.core import JsonValue, Operation, run_mutation

KIND = "openstack.compute.server"


def desired(name: str = "relay-worker", replicas: int = 2) -> DesiredResource:
    return DesiredResource(KIND, name, {"replicas": replicas, "image": "relay-1"})


def test_plan_classifies_create_update_and_noop() -> None:
    same = ResourceState(KIND, "same", {"replicas": 2, "image": "relay-1"})
    old = ResourceState(KIND, "old", {"replicas": 1, "image": "relay-1"})
    fake = FakeAdapter([same, old])
    reconciler = Reconciler({KIND: fake})

    plan = reconciler.plan([desired("new"), desired("old"), desired("same")])

    assert [change.action for change in plan] == [Action.CREATE, Action.UPDATE, Action.NOOP]


def test_apply_is_idempotent_and_reuses_a_stable_key() -> None:
    fake = FakeAdapter()
    reconciler = Reconciler({KIND: fake})
    resource = desired()

    first = reconciler.apply(reconciler.plan([resource]), timeout=10)
    second = reconciler.apply(reconciler.plan([resource]), timeout=10)

    assert first == second
    assert len(fake.put_calls) == 1
    assert fake.put_calls[0][1] == resource.idempotency_key
    assert "relay-worker" not in fake.put_calls[0][1]


@pytest.mark.parametrize("failure", [RequestLost("connect"), ResponseLost("read")])
def test_idempotent_reconcile_retries_network_loss(failure: Exception) -> None:
    fake = FakeAdapter(failures=[failure])
    sleeps: list[float] = []
    reconciler = Reconciler({KIND: fake}, sleep=sleeps.append)

    reconciler.apply([PlannedChange(Action.CREATE, desired())], timeout=10)

    assert len(fake.put_calls) == 2
    assert sleeps == [1.0]
    assert fake.put_calls[0][1] == fake.put_calls[1][1]


def test_rate_limit_honors_retry_after() -> None:
    fake = FakeAdapter(failures=[RateLimited(2.5)])
    sleeps: list[float] = []
    reconciler = Reconciler({KIND: fake}, sleep=sleeps.append)
    reconciler.apply([PlannedChange(Action.CREATE, desired())], timeout=10)
    assert sleeps == [2.5]


def test_retry_stops_at_attempt_budget() -> None:
    fake = FakeAdapter(failures=[RequestLost("one"), RequestLost("two")])
    reconciler = Reconciler({KIND: fake}, attempts=2, sleep=lambda _: None)
    with pytest.raises(RequestLost, match="two"):
        reconciler.apply([PlannedChange(Action.CREATE, desired())], timeout=10)


def test_deadline_prevents_retry() -> None:
    clock_values = iter([0.0, 0.0, 0.0])
    fake = FakeAdapter(failures=[RateLimited(20)])
    reconciler = Reconciler(
        {KIND: fake},
        monotonic=lambda: next(clock_values),
        sleep=lambda _: pytest.fail("must not sleep past deadline"),
    )
    with pytest.raises(RateLimited):
        reconciler.apply([PlannedChange(Action.CREATE, desired())], timeout=10)


def test_expired_reconciliation_does_not_start_work() -> None:
    clock_values = iter([1.0, 2.0])
    reconciler = Reconciler({KIND: FakeAdapter()}, monotonic=lambda: next(clock_values))
    with pytest.raises(DeadlineExceeded):
        reconciler.apply([PlannedChange(Action.CREATE, desired())], timeout=0.5)


def test_pagination_reads_each_page_once() -> None:
    fake = FakeAdapter([ResourceState(KIND, f"server-{number}", {}) for number in range(5)])
    found = Reconciler({KIND: fake}).list_all(KIND, page_size=2)
    assert [item.name for item in found] == [f"server-{number}" for number in range(5)]
    assert fake.page_calls == [None, "2", "4"]


def test_bad_inputs_and_missing_adapters_are_rejected() -> None:
    with pytest.raises(ValueError, match="attempts"):
        Reconciler({}, attempts=0)
    reconciler = Reconciler({})
    with pytest.raises(ValueError, match="no adapter"):
        reconciler.plan([desired()])
    with pytest.raises(ValueError, match="page_size"):
        Reconciler({KIND: FakeAdapter()}).list_all(KIND, 0)
    with pytest.raises(ValueError, match="timeout"):
        Reconciler({KIND: FakeAdapter()}).apply([], 0)


class Finished:
    def wait(self, deadline: float) -> ResourceState:
        return ResourceState(KIND, "done", {})


def test_request_loss_can_retry_but_response_loss_requires_idempotency() -> None:
    request_calls = 0

    def request_then_success() -> Operation:
        nonlocal request_calls
        request_calls += 1
        if request_calls == 1:
            raise RequestLost
        return Finished()

    assert run_mutation(request_then_success, idempotent=False).wait(1).name == "done"

    def ambiguous() -> Operation:
        raise ResponseLost

    with pytest.raises(UnknownOutcome):
        run_mutation(ambiguous, idempotent=False)


class FakeResource(dict[str, Any]):
    def __init__(self, name: str, **values: Any) -> None:
        super().__init__(name=name, **values)
        self.name = name

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


class FakeOpenStackService:
    def __init__(self) -> None:
        self.values: dict[str, FakeResource] = {}
        self.error: Exception | None = None

    def find_server(self, name: str, ignore_missing: bool = False) -> FakeResource | None:
        if self.error is not None:
            raise self.error
        return self.values.get(name)

    def create_server(self, **body: Any) -> FakeResource:
        values = {**body, "status": "ACTIVE"}
        name = str(values.pop("name"))
        value = FakeResource(
            name,
            **values,
        )
        self.values[value.name] = value
        return value

    def update_server(self, resource: FakeResource, **body: Any) -> FakeResource:
        resource.update(body)
        return resource

    def servers(self, **query: Any) -> list[FakeResource]:
        values = sorted(self.values.values(), key=lambda value: value.name)
        marker = query.get("marker")
        start = (
            0
            if marker is None
            else next(index + 1 for index, value in enumerate(values) if value.name == marker)
        )
        return values[start : start + int(query["limit"])]

    def get_container_metadata(self, name: str) -> FakeResource:
        if name not in self.values:
            raise openstack_exceptions.NotFoundException
        return self.values[name]

    def create_container(self, name: str, **metadata: Any) -> FakeResource:
        value = FakeResource(name, **metadata)
        self.values[name] = value
        return value

    def set_container_metadata(self, name: str, **metadata: Any) -> FakeResource:
        self.values[name].update(metadata)
        return self.values[name]

    def containers(self, **query: Any) -> list[FakeResource]:
        return list(self.values.values())[: int(query["limit"])]


class FakeConnection:
    def __init__(self) -> None:
        self.compute = FakeOpenStackService()
        self.network = FakeOpenStackService()
        self.image = FakeOpenStackService()
        self.block_storage = FakeOpenStackService()
        self.object_store = FakeOpenStackService()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_openstack_adapter_creates_waits_updates_and_pages() -> None:
    connection = FakeConnection()
    adapter = OpenStackAdapter(connection)  # type: ignore[arg-type]
    item = desired()

    created = adapter.put(item, item.idempotency_key).wait(10**12)
    assert created.name == item.name
    assert created.properties["status"] == "ACTIVE"

    changed = DesiredResource(KIND, item.name, {"flavor": "small"})
    adapter.put(changed, changed.idempotency_key).wait(10**12)
    assert connection.compute.values[item.name]["flavor"] == "small"

    connection.compute.create_server(name="worker-2")
    page = adapter.list_page(KIND, None, 1)
    assert len(page.items) == 1
    assert page.next_cursor == page.items[-1].name
    assert adapter.get(KIND, "missing") is None
    with pytest.raises(ValueError, match="unsupported"):
        adapter.get("openstack.unknown", "x")
    adapter.close()
    assert connection.closed


def test_openstack_projects_owned_fields_and_uses_container_signatures() -> None:
    connection = FakeConnection()
    adapter = OpenStackAdapter(connection)  # type: ignore[arg-type]
    resource = desired()
    adapter.put(resource, resource.idempotency_key).wait(10**12)
    connection.compute.values[resource.name]["host_id"] = "computed"
    assert Reconciler({resource.kind: adapter}).plan([resource])[0].action is Action.NOOP

    container = DesiredResource("openstack.object.container", "relay/tasks", {"read_ACL": ".r:*"})
    assert adapter.get(container.kind, container.name) is None
    state = adapter.put(container, container.idempotency_key).wait(10**12)
    assert state.properties["read_ACL"] == ".r:*"
    changed = DesiredResource(container.kind, container.name, {"read_ACL": "relay"})
    adapter.put(changed, changed.idempotency_key).wait(10**12)
    assert connection.object_store.values[container.name]["read_ACL"] == "relay"


def _http_exception(status: int) -> openstack_exceptions.HttpException:
    response = requests.Response()
    response.status_code = status
    return openstack_exceptions.HttpException(response=response)


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        (keystone_exceptions.ConnectFailure("connect"), RequestLost),
        (openstack_exceptions.InvalidResponse("truncated"), ResponseLost),
        (requests.exceptions.ConnectionError("ambiguous"), ResponseLost),
        (_http_exception(429), RateLimited),
        (_http_exception(503), ServiceUnavailable),
    ],
)
def test_openstack_translates_only_retryable_failure_categories(
    source: Exception, translated: type[Exception]
) -> None:
    assert isinstance(_translate(source), translated)


def test_openstack_keeps_terminal_sdk_errors_terminal() -> None:
    failure = openstack_exceptions.BadRequestException(response=requests.Response())
    failure.status_code = 400
    assert _translate(failure) is failure
    validation = openstack_exceptions.ValidationException("bad input")
    assert _translate(validation) is validation


class KubeObject:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        metadata = document.get("metadata", {})
        self.metadata = SimpleNamespace(name=metadata.get("name"), _continue=None)

    def to_dict(self) -> dict[str, Any]:
        return self.document


class FakeApps:
    def __init__(self) -> None:
        self.values: dict[str, KubeObject] = {}

    def read_namespaced_deployment(self, name: str, namespace: str) -> KubeObject:
        if name not in self.values:
            raise client.ApiException(status=404)
        return self.values[name]

    def create_namespaced_deployment(self, namespace: str, body: dict[str, Any]) -> None:
        spec = dict(body.get("spec", {}))
        body["metadata"]["generation"] = 2
        body["status"] = {
            "observed_generation": 2,
            "updated_replicas": spec.get("replicas", 1),
            "available_replicas": spec.get("replicas", 1),
        }
        self.values[str(body["metadata"]["name"])] = KubeObject(body)

    def patch_namespaced_deployment(self, name: str, namespace: str, body: dict[str, Any]) -> None:
        self.create_namespaced_deployment(namespace, body)

    def list_namespaced_deployment(self, namespace: str, limit: int, _continue: str | None) -> Any:
        return SimpleNamespace(
            items=list(self.values.values())[:limit],
            metadata=SimpleNamespace(_continue=None),
        )


def test_kubernetes_adapter_creates_patches_checks_readiness_and_lists() -> None:
    apps = FakeApps()
    adapter = KubernetesAdapter(apps, SimpleNamespace(), namespace="relay")  # type: ignore[arg-type]
    resource = DesiredResource(
        "kubernetes.deployment",
        "relay",
        {
            "spec": {"replicas": 2},
            "metadata": {
                "labels": {"app": "relay"},
                "annotations": {"operator.example/owner": "chapter-36"},
            },
        },
    )
    state = adapter.put(resource, resource.idempotency_key).wait(10**12)
    assert state.properties["status"] == {
        "observed_generation": 2,
        "updated_replicas": 2,
        "available_replicas": 2,
    }
    annotations = apps.values["relay"].document["metadata"]["annotations"]
    assert annotations["relay.example/idempotency-key"] == resource.idempotency_key
    assert annotations["operator.example/owner"] == "chapter-36"
    apps.values["relay"].document["metadata"]["resource_version"] = "computed"
    assert Reconciler({resource.kind: adapter}).plan([resource])[0].action is Action.NOOP
    adapter.put(resource, resource.idempotency_key).wait(10**12)
    assert adapter.list_page(resource.kind, None, 10).items[0].name == "relay"
    with pytest.raises(ValueError, match="unsupported"):
        adapter.get("kubernetes.job", "relay")


def test_kubernetes_deployment_readiness_requires_current_generation_and_replicas() -> None:
    base: dict[str, JsonValue] = {
        "metadata": {"generation": 4},
        "spec": {"replicas": 3},
        "status": {
            "observed_generation": 4,
            "updated_replicas": 3,
            "available_replicas": 3,
        },
    }
    ready = ResourceState("kubernetes.deployment", "relay", base)
    assert KubernetesAdapter._ready(ready)
    for field, value in [
        ("observed_generation", 3),
        ("updated_replicas", 2),
        ("available_replicas", 2),
    ]:
        current_status = base["status"]
        assert isinstance(current_status, dict)
        document: dict[str, JsonValue] = {
            **base,
            "status": {**current_status, field: value},
        }
        assert not KubernetesAdapter._ready(
            ResourceState("kubernetes.deployment", "relay", document)
        )


def harbor_client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_redfish_reads_bounded_system_inventory_across_pages() -> None:
    documents: dict[str, dict[str, Any]] = {
        "/redfish/v1/": {
            "@odata.id": "/redfish/v1/",
            "Systems": {"@odata.id": "/redfish/v1/Systems?view=inventory"},
        },
        "/redfish/v1/Systems?view=inventory": {
            "Members": [{"@odata.id": "/redfish/v1/Systems/node-1"}],
            "Members@odata.nextLink": "/redfish/v1/Systems?page=2",
        },
        "/redfish/v1/Systems?page=2": {
            "Members": [{"@odata.id": "/redfish/v1/Systems/node-2"}],
        },
        "/redfish/v1/Systems/node-1": {
            "Id": "node-1",
            "Name": "GPU node 1",
            "Model": "Rack server",
            "Manufacturer": "Example Systems",
            "SerialNumber": "EXAMPLE-001",
            "PowerState": "On",
            "Status": {"Health": "OK"},
        },
        "/redfish/v1/Systems/node-2": {
            "Id": "node-2",
            "Name": "GPU node 2",
            "Model": "Rack server",
            "Manufacturer": "Example Systems",
            "SerialNumber": "EXAMPLE-002",
            "PowerState": "Off",
            "Status": {"Health": "Warning"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.raw_path.decode()
        return httpx.Response(200, json=documents[key])

    client = RedfishClient(
        "https://bmc.example",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    systems = client.list_systems()

    assert [(system.system_id, system.power_state) for system in systems] == [
        ("node-1", "On"),
        ("node-2", "Off"),
    ]
    assert client.get_system("node-1").health == "OK"
    client.close()


def test_redfish_rejects_insecure_urls_cross_origin_links_and_bad_shapes() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        RedfishClient("http://bmc.example", httpx.Client())
    with pytest.raises(ValueError, match="credentials"):
        RedfishClient("https://user:secret@bmc.example", httpx.Client())

    def cross_origin(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redfish/v1/":
            return httpx.Response(
                200,
                json={"Systems": {"@odata.id": "/redfish/v1/Systems"}},
            )
        return httpx.Response(
            200,
            json={
                "Members": [],
                "Members@odata.nextLink": "https://attacker.example/redfish/v1/Systems",
            },
        )

    client = RedfishClient(
        "https://bmc.example",
        httpx.Client(transport=httpx.MockTransport(cross_origin)),
    )
    with pytest.raises(RedfishProtocolError, match="changed origin"):
        client.list_systems()


def test_harbor_adapter_requires_https_and_never_logs_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HarborAdapter("http://registry.example", harbor_client(lambda _: httpx.Response(200)))
    with pytest.raises(ValueError, match="trust bundle"):
        HarborAdapter.with_mtls(
            "https://registry.example", certificate=("client.crt", "client.key"), trust_bundle=""
        )
    assert "client.key" not in caplog.text


def test_harbor_create_update_pagination_and_rate_limit() -> None:
    projects: dict[str, dict[str, Any]] = {}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/v2.0/projects" and request.method == "GET":
            return httpx.Response(
                200,
                json=list(projects.values()),
                headers={"X-Total-Count": str(len(projects))},
            )
        name = path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return (
                httpx.Response(200, json=projects[name])
                if name in projects
                else httpx.Response(404)
            )
        body = __import__("json").loads(request.content)
        if request.method == "POST":
            projects[str(body["project_name"])] = {"name": body["project_name"], **body}
        else:
            projects[name] = {"name": name, **body}
        return httpx.Response(201 if request.method == "POST" else 200)

    client_ = harbor_client(handler)
    adapter = HarborAdapter("https://registry.example/", client_)
    resource = DesiredResource("harbor.project", "relay", {"public": False})
    assert adapter.get(resource.kind, resource.name) is None
    assert adapter.put(resource, resource.idempotency_key).wait(10**12).name == "relay"
    updated = DesiredResource(resource.kind, resource.name, {"public": True})
    adapter.put(updated, updated.idempotency_key).wait(10**12)
    assert projects["relay"]["public"] is True
    create_request = next(request for request in requests if request.method == "POST")
    assert create_request.headers["Idempotency-Key"] == resource.idempotency_key
    assert adapter.list_page(resource.kind, None, 1).items[0].name == "relay"
    with pytest.raises(ValueError, match="unsupported"):
        adapter.get("harbor.repository", "relay")
    adapter.close()

    limited = HarborAdapter(
        "https://registry.example",
        harbor_client(lambda _: httpx.Response(429, headers={"Retry-After": "3"})),
    )
    with pytest.raises(RateLimited) as found:
        limited.get("harbor.project", "relay")
    assert found.value.retry_after == 3


def test_harbor_transport_distinguishes_request_and_response_loss() -> None:
    request_adapter = HarborAdapter(
        "https://registry.example",
        harbor_client(lambda request: (_ for _ in ()).throw(httpx.ConnectError("no route"))),
    )
    with pytest.raises(RequestLost):
        request_adapter.get("harbor.project", "relay")

    response_adapter = HarborAdapter(
        "https://registry.example",
        harbor_client(lambda request: (_ for _ in ()).throw(httpx.ReadError("lost reply"))),
    )
    with pytest.raises(ResponseLost):
        response_adapter.get("harbor.project", "relay")


def test_harbor_conflict_reads_owned_state_and_converges_with_encoded_name() -> None:
    requests_seen: list[httpx.Request] = []
    current = {"name": "relay/blue", "public": False, "project_id": 17}

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.method == "POST":
            return httpx.Response(409)
        if request.method == "PUT":
            current["public"] = True
            return httpx.Response(200)
        if request.method == "GET":
            if len(requests_seen) == 1:
                return httpx.Response(404)
            return httpx.Response(200, json=current)
        raise AssertionError(request.method)

    adapter = HarborAdapter("https://registry.example", harbor_client(handler))
    desired_project = DesiredResource("harbor.project", "relay/blue", {"public": True})
    result = adapter.put(desired_project, desired_project.idempotency_key).wait(10**12)

    assert result.properties["public"] is True
    assert any(request.method == "PUT" for request in requests_seen)
    project_requests = [request for request in requests_seen if request.method in {"GET", "PUT"}]
    assert all(b"relay%2Fblue" in request.url.raw_path for request in project_requests)
    assert (
        Reconciler({desired_project.kind: adapter}).plan([desired_project])[0].action is Action.NOOP
    )


def test_harbor_conflict_raises_when_state_cannot_be_read_or_converged() -> None:
    responses = iter(
        [
            httpx.Response(404),
            httpx.Response(409),
            httpx.Response(404),
        ]
    )
    adapter = HarborAdapter("https://registry.example", harbor_client(lambda _: next(responses)))
    project = DesiredResource("harbor.project", "relay", {"public": False})
    with pytest.raises(ResourceConflict, match="absent"):
        adapter.put(project, project.idempotency_key)

    current = {"name": "relay", "public": True}

    def update_conflict(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=current)
        return httpx.Response(409)

    adapter = HarborAdapter("https://registry.example", harbor_client(update_conflict))
    with pytest.raises(ResourceConflict, match="could not converge"):
        adapter.put(project, project.idempotency_key)

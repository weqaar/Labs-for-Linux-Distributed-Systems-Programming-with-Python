"""Adapters for OpenStack, Kubernetes and Harbor."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

import httpx
from keystoneauth1 import exceptions as keystone_exceptions
from kubernetes import client
from openstack import exceptions as openstack_exceptions
from openstack.connection import Connection
from requests import exceptions as requests_exceptions

from .core import (
    DeadlineExceeded,
    DesiredResource,
    JsonValue,
    Operation,
    Page,
    RateLimited,
    RequestLost,
    ResourceConflict,
    ResourceState,
    ResponseLost,
    ServiceUnavailable,
)


def _plain(value: Any) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return str(value)


def _integer(value: object, default: int) -> int:
    if isinstance(value, str | int | float):
        return int(value)
    return default


def _project_value(observed: JsonValue, desired: JsonValue) -> JsonValue:
    if isinstance(desired, dict):
        if not isinstance(observed, dict):
            return observed
        return {
            key: _project_value(observed[key], value)
            for key, value in desired.items()
            if key in observed
        }
    if isinstance(desired, list):
        return observed
    return observed


def _project_properties(
    observed: Mapping[str, JsonValue], desired: Mapping[str, JsonValue]
) -> Mapping[str, JsonValue]:
    return {
        key: _project_value(observed[key], value)
        for key, value in desired.items()
        if key in observed
    }


def _resource_name(resource: Any) -> str:
    name = getattr(resource, "name", None)
    if name is None and isinstance(resource, Mapping):
        name = resource.get("name")
    if name is None:
        raise ValueError("listed resource has no name")
    return str(name)


class _PollingOperation:
    def __init__(
        self,
        read: Callable[[], ResourceState],
        ready: Callable[[ResourceState], bool],
        *,
        interval: float = 1.0,
    ) -> None:
        self._read = read
        self._ready = ready
        self._interval = interval

    def wait(self, deadline: float) -> ResourceState:
        while time.monotonic() < deadline:
            state = self._read()
            if self._ready(state):
                return state
            time.sleep(min(self._interval, max(0.0, deadline - time.monotonic())))
        raise DeadlineExceeded("cloud operation did not become ready")


def _translate(error: Exception) -> Exception:
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        return RequestLost(str(error))
    if isinstance(
        error,
        (httpx.ReadError, httpx.ReadTimeout, httpx.WriteError, httpx.RemoteProtocolError),
    ):
        return ResponseLost(str(error))
    if isinstance(
        error,
        (
            keystone_exceptions.ConnectFailure,
            keystone_exceptions.ConnectTimeout,
            requests_exceptions.ConnectTimeout,
        ),
    ):
        return RequestLost(str(error))
    if isinstance(
        error,
        (
            requests_exceptions.ReadTimeout,
            requests_exceptions.ConnectionError,
            requests_exceptions.ChunkedEncodingError,
            openstack_exceptions.InvalidResponse,
        ),
    ):
        return ResponseLost(str(error))
    if isinstance(error, openstack_exceptions.HttpException):
        if error.status_code == 429:
            response = error.response
            retry_after = (
                float(response.headers.get("Retry-After", 0)) if response is not None else 0.0
            )
            return RateLimited(retry_after)
        if error.status_code in {502, 503, 504}:
            return ServiceUnavailable(str(error))
    return error


class OpenStackAdapter:
    """OpenStackSDK compute, network, image, block and object adapter."""

    _services = {
        "openstack.compute.server": ("compute", "server"),
        "openstack.network.network": ("network", "network"),
        "openstack.image.image": ("image", "image"),
        "openstack.block.volume": ("block_storage", "volume"),
        "openstack.object.container": ("object_store", "container"),
    }

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def close(self) -> None:
        self._connection.close()

    def _parts(self, kind: str) -> tuple[Any, str]:
        try:
            service_name, noun = self._services[kind]
        except KeyError as error:
            raise ValueError(f"unsupported OpenStack kind {kind!r}") from error
        return getattr(self._connection, service_name), noun

    def get(self, kind: str, name: str) -> ResourceState | None:
        service, noun = self._parts(kind)
        try:
            if noun == "container":
                raw = service.get_container_metadata(name)
            else:
                raw = getattr(service, f"find_{noun}")(name, ignore_missing=True)
        except openstack_exceptions.NotFoundException:
            return None
        except Exception as error:
            raise _translate(error) from error
        if raw is None:
            return None
        document = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
        return ResourceState(kind, name, _plain(document))  # type: ignore[arg-type]

    def put(self, desired: DesiredResource, idempotency_key: str) -> Operation:
        service, noun = self._parts(desired.kind)
        existing = self.get(desired.kind, desired.name)
        body = dict(desired.properties)
        try:
            if noun == "container":
                if existing is None:
                    service.create_container(desired.name, **body)
                else:
                    service.set_container_metadata(desired.name, **body)
            elif existing is None:
                getattr(service, f"create_{noun}")(name=desired.name, **body)
            else:
                resource = getattr(service, f"find_{noun}")(desired.name)
                getattr(service, f"update_{noun}")(resource, **body)
        except Exception as error:
            raise _translate(error) from error
        return _PollingOperation(
            lambda: self._required(desired.kind, desired.name),
            self._ready,
        )

    def _required(self, kind: str, name: str) -> ResourceState:
        state = self.get(kind, name)
        if state is None:
            raise RuntimeError(f"{kind} {name!r} disappeared")
        return state

    @staticmethod
    def _ready(state: ResourceState) -> bool:
        expected = {
            "openstack.compute.server": {"ACTIVE"},
            "openstack.image.image": {"ACTIVE"},
            "openstack.block.volume": {"AVAILABLE", "IN-USE"},
        }.get(state.kind)
        if expected is None:
            return True
        status = str(state.properties.get("status", "")).upper()
        if status in {"ERROR", "FAILED", "KILLED"}:
            raise RuntimeError(f"{state.kind} {state.name!r} entered {status}")
        return status in expected

    def list_page(self, kind: str, cursor: str | None, limit: int) -> Page:
        service, noun = self._parts(kind)
        plural = "containers" if noun == "container" else f"{noun}s"
        query: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            query["marker"] = cursor
        try:
            raw_items = list(getattr(service, plural)(**query))
        except Exception as error:
            raise _translate(error) from error
        items = [
            ResourceState(
                kind,
                _resource_name(item),
                _plain(item.to_dict() if hasattr(item, "to_dict") else dict(item)),  # type: ignore[arg-type]
            )
            for item in raw_items
        ]
        next_cursor = items[-1].name if len(items) == limit else None
        return Page(items, next_cursor)

    def project(self, desired: DesiredResource, current: ResourceState) -> Mapping[str, JsonValue]:
        return _project_properties(current.properties, desired.properties)


class KubernetesAdapter:
    """Kubernetes deployment, service, config map and secret adapter."""

    _readers = {
        "kubernetes.deployment": ("apps", "read_namespaced_deployment"),
        "kubernetes.service": ("core", "read_namespaced_service"),
        "kubernetes.configmap": ("core", "read_namespaced_config_map"),
        "kubernetes.secret": ("core", "read_namespaced_secret"),
    }

    def __init__(
        self,
        apps: client.AppsV1Api,
        core: client.CoreV1Api,
        *,
        namespace: str,
    ) -> None:
        self._apps = apps
        self._core = core
        self._namespace = namespace

    def _api_method(self, kind: str, prefix: str) -> Callable[..., Any]:
        try:
            target_name, reader = self._readers[kind]
        except KeyError as error:
            raise ValueError(f"unsupported Kubernetes kind {kind!r}") from error
        target = self._apps if target_name == "apps" else self._core
        method = reader.replace("read_", prefix, 1)
        return getattr(target, method)

    def get(self, kind: str, name: str) -> ResourceState | None:
        try:
            value = self._api_method(kind, "read_")(name, self._namespace)
        except client.ApiException as error:
            if error.status == 404:
                return None
            if error.status == 429:
                headers = error.headers or {}
                raise RateLimited(float(headers.get("Retry-After", 1))) from error
            raise
        document = value.to_dict()
        properties = _plain(document)
        return ResourceState(kind, name, properties)  # type: ignore[arg-type]

    def put(self, desired: DesiredResource, idempotency_key: str) -> Operation:
        body: dict[str, Any] = dict(desired.properties)
        raw_metadata = body.get("metadata", {})
        metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        raw_annotations = metadata.get("annotations", {})
        annotations = dict(raw_annotations) if isinstance(raw_annotations, Mapping) else {}
        annotations["relay.example/idempotency-key"] = idempotency_key
        metadata.update(
            {
                "name": desired.name,
                "namespace": self._namespace,
                "annotations": annotations,
            }
        )
        body["metadata"] = metadata
        if self.get(desired.kind, desired.name) is None:
            self._api_method(desired.kind, "create_")(self._namespace, body)
        else:
            self._api_method(desired.kind, "patch_")(desired.name, self._namespace, body)
        return _PollingOperation(
            lambda: self._required(desired.kind, desired.name),
            self._ready,
        )

    def _required(self, kind: str, name: str) -> ResourceState:
        state = self.get(kind, name)
        if state is None:
            raise RuntimeError(f"{kind} {name!r} disappeared")
        return state

    @staticmethod
    def _ready(state: ResourceState) -> bool:
        if state.kind != "kubernetes.deployment":
            return True
        status = state.properties.get("status")
        spec = state.properties.get("spec")
        metadata = state.properties.get("metadata")
        if (
            not isinstance(status, dict)
            or not isinstance(spec, dict)
            or not isinstance(metadata, dict)
        ):
            return False
        desired = _integer(spec.get("replicas"), 1)
        generation = _integer(metadata.get("generation"), 0)
        return (
            _integer(status.get("observed_generation"), -1) >= generation
            and _integer(status.get("updated_replicas"), 0) >= desired
            and _integer(status.get("available_replicas"), 0) >= desired
        )

    def list_page(self, kind: str, cursor: str | None, limit: int) -> Page:
        method = self._api_method(kind, "list_")
        result = method(self._namespace, limit=limit, _continue=cursor)
        items = [
            ResourceState(kind, item.metadata.name, _plain(item.to_dict()))  # type: ignore[arg-type]
            for item in result.items
        ]
        return Page(items, result.metadata._continue or None)

    def project(self, desired: DesiredResource, current: ResourceState) -> Mapping[str, JsonValue]:
        return _project_properties(current.properties, desired.properties)


class HarborAdapter:
    """Typed Harbor v2 project API over one shared HTTP client."""

    kind = "harbor.project"

    def __init__(self, base_url: str, client_: httpx.Client) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Harbor base URL must use HTTPS")
        self._base_url = base_url.rstrip("/")
        self._client = client_

    @classmethod
    def with_mtls(
        cls,
        base_url: str,
        *,
        certificate: tuple[str, str],
        trust_bundle: str,
        timeout: float = 10.0,
    ) -> HarborAdapter:
        if not trust_bundle:
            raise ValueError("a CA trust bundle is required")
        transport = httpx.HTTPTransport(verify=trust_bundle, cert=certificate, retries=0)
        return cls(base_url, httpx.Client(transport=transport, timeout=timeout))

    def close(self) -> None:
        self._client.close()

    def get(self, kind: str, name: str) -> ResourceState | None:
        self._check_kind(kind)
        response = self._request("GET", self._project_path(name))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        document = response.json()
        return ResourceState(kind, name, _plain(document))  # type: ignore[arg-type]

    def put(self, desired: DesiredResource, idempotency_key: str) -> Operation:
        self._check_kind(desired.kind)
        if self.get(desired.kind, desired.name) is None:
            response = self._request(
                "POST",
                "/api/v2.0/projects",
                json={"project_name": desired.name, **desired.properties},
                headers={"Idempotency-Key": idempotency_key},
            )
            if response.status_code == 409:
                current = self.get(desired.kind, desired.name)
                if current is None:
                    raise ResourceConflict(
                        f"Harbor reported a conflict for absent project {desired.name!r}"
                    )
                if self.project(desired, current) != desired.properties:
                    self._update(desired, idempotency_key)
            else:
                response.raise_for_status()
        else:
            self._update(desired, idempotency_key)
        return _PollingOperation(lambda: self._required(desired.name), lambda _: True)

    def _update(self, desired: DesiredResource, idempotency_key: str) -> None:
        response = self._request(
            "PUT",
            self._project_path(desired.name),
            json=dict(desired.properties),
            headers={"Idempotency-Key": idempotency_key},
        )
        if response.status_code == 409:
            raise ResourceConflict(f"Harbor project {desired.name!r} could not converge")
        response.raise_for_status()

    def _required(self, name: str) -> ResourceState:
        state = self.get(self.kind, name)
        if state is None:
            raise RuntimeError(f"Harbor project {name!r} disappeared")
        return state

    def list_page(self, kind: str, cursor: str | None, limit: int) -> Page:
        self._check_kind(kind)
        page = int(cursor or "1")
        response = self._request(
            "GET", "/api/v2.0/projects", params={"page": page, "page_size": limit}
        )
        response.raise_for_status()
        values = response.json()
        items = [
            ResourceState(kind, str(item["name"]), _plain(item))  # type: ignore[arg-type]
            for item in values
        ]
        total = int(response.headers.get("X-Total-Count", len(items)))
        next_cursor = str(page + 1) if page * limit < total else None
        return Page(items, next_cursor)

    def project(self, desired: DesiredResource, current: ResourceState) -> Mapping[str, JsonValue]:
        return _project_properties(current.properties, desired.properties)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, self._base_url + path, **kwargs)
        except Exception as error:
            raise _translate(error) from error
        if response.status_code == 429:
            raise RateLimited(float(response.headers.get("Retry-After", 1)))
        return response

    def _check_kind(self, kind: str) -> None:
        if kind != self.kind:
            raise ValueError(f"unsupported Harbor kind {kind!r}")

    @staticmethod
    def _project_path(name: str) -> str:
        return f"/api/v2.0/projects/{quote(name, safe='')}"

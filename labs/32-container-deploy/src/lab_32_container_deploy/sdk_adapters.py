"""Typed Docker and Kubernetes SDK boundaries for deploying relay."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

_IMAGE_BY_DIGEST = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


class ContainerHandle(Protocol):
    """Container operations used by the relay smoke check."""

    @property
    def id(self) -> str: ...

    def logs(self) -> bytes: ...

    def wait(self, *, timeout: int) -> Mapping[str, object]: ...

    def remove(self, *, force: bool = False) -> None: ...


class ContainerCollection(Protocol):
    """Subset of Docker's container collection used by relay."""

    def run(
        self,
        image: str,
        command: Sequence[str],
        *,
        detach: bool,
        name: str,
        read_only: bool,
        user: str,
        cap_drop: Sequence[str],
        security_opt: Sequence[str],
        network_disabled: bool,
    ) -> ContainerHandle: ...


class DockerClient(Protocol):
    """Subset of DockerClient used by relay."""

    @property
    def containers(self) -> ContainerCollection: ...


class KubernetesAppsApi(Protocol):
    """Subset of AppsV1Api used to promote relay."""

    def patch_namespaced_deployment(
        self,
        *,
        name: str,
        namespace: str,
        body: Mapping[str, object],
        field_manager: str,
    ) -> object: ...

    def list_namespaced_deployment(
        self,
        namespace: str,
        *,
        field_selector: str,
        timeout_seconds: int,
    ) -> object: ...


class KubernetesWatch(Protocol):
    """Subset of kubernetes.watch.Watch used by relay."""

    def stream(
        self,
        function: Callable[..., object],
        namespace: str,
        *,
        field_selector: str,
        timeout_seconds: int,
    ) -> Iterable[Mapping[str, object]]: ...


@dataclass(frozen=True)
class SmokeResult:
    """Result of running relayctl from the release image."""

    container_id: str
    status_code: int
    output: str


@dataclass(frozen=True)
class Promotion:
    """The exact Kubernetes deployment mutation requested by relay."""

    namespace: str
    deployment: str
    image: str


def docker_client_from_env() -> DockerClient:
    """Create the real Docker SDK client from standard environment settings."""

    docker = import_module("docker")
    return cast(DockerClient, docker.from_env())


def run_relay_smoke_check(
    client: DockerClient,
    image: str,
    *,
    timeout_seconds: int = 30,
) -> SmokeResult:
    """Run relayctl from an immutable image, collect evidence, then clean up."""

    _require_digest(image)
    container = client.containers.run(
        image,
        ("relayctl", "--version"),
        detach=True,
        name="relay-release-smoke",
        read_only=True,
        user="10001:10001",
        cap_drop=("ALL",),
        security_opt=("no-new-privileges",),
        network_disabled=True,
    )
    try:
        result = container.wait(timeout=timeout_seconds)
        status = result.get("StatusCode")
        if not isinstance(status, int):
            raise RuntimeError("Docker wait response did not contain an integer StatusCode")
        output = container.logs().decode("utf-8", errors="replace").strip()
        if status != 0:
            raise RuntimeError(f"relay smoke container exited {status}: {output}")
        return SmokeResult(container.id, status, output)
    finally:
        container.remove(force=True)


def kubernetes_apps_api(*, in_cluster: bool) -> KubernetesAppsApi:
    """Load Kubernetes credentials and return the real AppsV1Api client."""

    config = import_module("kubernetes.config")
    client = import_module("kubernetes.client")
    if in_cluster:
        config.load_incluster_config()
    else:
        config.load_kube_config()
    return cast(KubernetesAppsApi, client.AppsV1Api())


def kubernetes_watch() -> KubernetesWatch:
    """Return the real Kubernetes watch client."""

    watch = import_module("kubernetes.watch")
    return cast(KubernetesWatch, watch.Watch())


def promote_relay(
    api: KubernetesAppsApi,
    image: str,
    *,
    namespace: str = "relay",
    deployment: str = "relay",
) -> Promotion:
    """Patch relay to an immutable image using the Apps v1 API."""

    _require_digest(image)
    body: Mapping[str, object] = {
        "spec": {
            "template": {
                "metadata": {"annotations": {"relay.packetfive.com/release-image": image}},
                "spec": {"containers": [{"name": "relay", "image": image}]},
            }
        }
    }
    api.patch_namespaced_deployment(
        name=deployment,
        namespace=namespace,
        body=body,
        field_manager="relay-release",
    )
    return Promotion(namespace, deployment, image)


def watch_relay_rollout(
    api: KubernetesAppsApi,
    watcher: KubernetesWatch,
    *,
    namespace: str = "relay",
    deployment: str = "relay",
    timeout_seconds: int = 60,
) -> bool:
    """Return whether a watched relay deployment reaches its desired replicas."""

    events = watcher.stream(
        api.list_namespaced_deployment,
        namespace,
        field_selector=f"metadata.name={deployment}",
        timeout_seconds=timeout_seconds,
    )
    for event in events:
        candidate = event.get("object")
        spec = _field(candidate, "spec")
        status = _field(candidate, "status")
        desired = _field(spec, "replicas")
        observed = _field(status, "observed_generation")
        generation = _field(_field(candidate, "metadata"), "generation")
        available = _field(status, "available_replicas")
        updated = _field(status, "updated_replicas")
        if (
            isinstance(desired, int)
            and isinstance(observed, int)
            and isinstance(generation, int)
            and observed >= generation
            and available == desired
            and updated == desired
        ):
            return True
    return False


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _require_digest(image: str) -> None:
    if _IMAGE_BY_DIGEST.fullmatch(image) is None:
        raise ValueError("relay image must be pinned by sha256 digest")

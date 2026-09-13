"""Tests for transactional SigRaft configuration reload."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from opentelemetry.sdk.trace.sampling import Decision

from lab_39_sigraft_service.config import (
    ConfigManager,
    ConfigWatcher,
    PreparedChange,
    RequestLimits,
    ServiceSettings,
)
from lab_39_sigraft_service.sigraft_service import SigRaftService, run_server
from lab_39_sigraft_service.telemetry import TelemetryRuntime
from lab_39_sigraft_service.version import __version__

DIGEST = "sha256:" + "a" * 64


def configuration(
    *,
    body_limit: int = 4096,
    action_limit: int = 100,
    ratio: float = 1.0,
    exporter: str = "memory",
    endpoint: str = "http://collector:4317",
    environment: str = "test",
) -> bytes:
    return f"""\
[schema]
version = 1
[identity]
deployment_environment = "{environment}"
service_instance_id = "test-instance"
[requests]
max_body_bytes = {body_limit}
max_action_chars = {action_limit}
[telemetry]
sample_ratio = {ratio}
exporter = "{exporter}"
otlp_endpoint = "{endpoint}"
otlp_insecure = true
[analysis]
enabled = true
observations_path = "package:observations.csv"
refresh_interval_seconds = 60.0
""".encode()


def configured_service(content: bytes) -> SigRaftService:
    manager = ConfigManager.from_bytes(content)
    telemetry = TelemetryRuntime.in_memory(
        version=__version__,
        environment="test",
        instance_id="config-test",
        release_digest=DIGEST,
        sample_ratio=manager.snapshot.settings.telemetry.sample_ratio,
    )
    return SigRaftService(DIGEST, manager, telemetry)


def test_successful_reload_updates_limits_sampler_generation_and_revision() -> None:
    service = configured_service(configuration())
    assert service.config_manager is not None
    assert service.telemetry is not None
    candidate = configuration(action_limit=3, ratio=0.0)

    assert service.config_manager.reload_bytes(candidate) is True

    snapshot = service.config_manager.snapshot
    assert snapshot.generation == 2
    assert snapshot.revision == hashlib.sha256(candidate).hexdigest()
    assert service.telemetry.sampler.ratio == 0.0
    decision = service.telemetry.sampler.should_sample(None, 1, "next-request")
    assert decision.decision is Decision.DROP
    with pytest.raises(ValueError, match="configured limit"):
        service.submit_task("four")
    assert service.metadata()["configuration"]["last_result"] == "applied"
    service.shutdown()


def test_invalid_candidate_retains_previous_generation_and_values() -> None:
    service = configured_service(configuration())
    assert service.config_manager is not None
    original = service.config_manager.snapshot

    assert service.config_manager.reload_bytes(b"[requests]\nmax_body_bytes = -1") is False

    assert service.config_manager.snapshot == original
    status = service.config_manager.status
    assert status.generation == 1
    assert status.last_result == "failed"
    assert status.last_error == "invalid configuration candidate"
    service.shutdown()


def test_body_limit_changes_live_without_restarting_http() -> None:
    service = configured_service(configuration(body_limit=256, action_limit=500))
    hosted = run_server(service)
    payload = b'{"action":"' + (b"x" * 300) + b'"}'

    try:
        with pytest.raises(HTTPError) as rejected:
            urlopen(
                Request(
                    f"{hosted.base_url}/tasks",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            )
        assert rejected.value.code == 413
        assert service.config_manager is not None
        assert service.config_manager.reload_bytes(configuration(body_limit=1024, action_limit=500))
        with urlopen(
            Request(
                f"{hosted.base_url}/tasks",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        ) as accepted:
            assert accepted.status == 202
    finally:
        hosted.close()


@dataclass
class StateChange:
    state: dict[str, int]
    old: int
    new: int
    fail_commit: bool = False
    fail_rollback: bool = False

    def commit(self) -> None:
        self.state["value"] = self.new
        if self.fail_commit:
            raise RuntimeError("commit rejected")

    def rollback(self) -> None:
        self.state["value"] = self.old
        if self.fail_rollback:
            raise RuntimeError("rollback rejected")


class StateSubsystem:
    def __init__(
        self,
        name: str,
        state: dict[str, int],
        *,
        fail_prepare: bool = False,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.name = name
        self.state = state
        self.fail_prepare = fail_prepare
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback

    def prepare(self, candidate: ServiceSettings, current: ServiceSettings) -> PreparedChange:
        del current
        if self.fail_prepare:
            raise RuntimeError("candidate rejected")
        return StateChange(
            self.state,
            self.state["value"],
            candidate.requests.max_action_chars,
            self.fail_commit,
            self.fail_rollback,
        )


@pytest.mark.parametrize("phase", ["prepare", "commit"])
def test_subsystem_failure_rolls_back_every_change(phase: str) -> None:
    manager = ConfigManager.from_bytes(configuration(action_limit=10))
    first = {"value": 10}
    second = {"value": 10}
    manager.register(StateSubsystem("analytics-refresh", first))
    manager.register(
        StateSubsystem(
            "failing",
            second,
            fail_prepare=phase == "prepare",
            fail_commit=phase == "commit",
        )
    )

    assert manager.reload_bytes(configuration(action_limit=20)) is False

    assert first["value"] == 10
    assert second["value"] == 10
    assert manager.snapshot.generation == 1


def test_restart_required_fields_are_reported_without_values() -> None:
    manager = ConfigManager.from_bytes(configuration())

    assert manager.reload_bytes(
        configuration(
            exporter="otlp",
            endpoint="http://new-collector:4317",
            environment="production",
        )
    )

    status = manager.status.as_dict()
    assert status["restart_required"] == [
        "identity.deployment_environment",
        "telemetry.exporter",
        "telemetry.otlp_endpoint",
    ]
    assert "new-collector" not in str(status)


def test_rollback_failure_is_reported_without_escaping_reload() -> None:
    manager = ConfigManager.from_bytes(configuration(action_limit=10))
    state = {"value": 10}
    manager.register(
        StateSubsystem(
            "failing",
            state,
            fail_commit=True,
            fail_rollback=True,
        )
    )

    assert manager.reload_bytes(configuration(action_limit=20)) is False

    assert manager.snapshot.generation == 1
    assert manager.status.last_result == "failed"
    assert manager.status.last_error == "subsystem commit and rollback failed"


class MutableProbe:
    def __init__(self) -> None:
        self.value = 1

    def token(self, path: Path) -> object:
        del path
        return self.value


def test_file_change_probe_drives_reload_without_sleep(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sigraft.toml"
    initial = configuration(action_limit=10)
    path.write_bytes(initial)
    manager = ConfigManager.from_bytes(initial)
    limits = RequestLimits(manager.snapshot.settings.requests)
    manager.register(limits)
    probe = MutableProbe()
    watcher = ConfigWatcher(manager, path, probe=probe)

    assert watcher.poll_once() is False
    path.write_bytes(configuration(action_limit=20))
    probe.value = 2
    assert watcher.poll_once() is True
    assert limits.current.max_action_chars == 20
    assert manager.snapshot.generation == 2


def test_watcher_thread_stops_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "sigraft.toml"
    content = configuration()
    path.write_bytes(content)
    watcher = ConfigWatcher(ConfigManager.from_bytes(content), path, interval=60)

    watcher.start()
    assert watcher.running
    watcher.stop()

    assert watcher.running is False


class NotifyingSubsystem:
    name = "analytics-refresh"

    def __init__(self, applied: Event) -> None:
        self.applied = applied

    def prepare(self, candidate: ServiceSettings, current: ServiceSettings) -> PreparedChange:
        del candidate, current
        return NotifyingChange(self.applied)


@dataclass
class NotifyingChange:
    applied: Event

    def commit(self) -> None:
        self.applied.set()

    def rollback(self) -> None:
        pass


def test_requested_reload_runs_in_watcher_thread_without_sleep(tmp_path: Path) -> None:
    path = tmp_path / "sigraft.toml"
    content = configuration(action_limit=10)
    path.write_bytes(content)
    manager = ConfigManager.from_bytes(content)
    applied = Event()
    manager.register(NotifyingSubsystem(applied))
    watcher = ConfigWatcher(manager, path, interval=60, poll_changes=False)
    watcher.start()
    path.write_bytes(configuration(action_limit=20))

    watcher.request_reload()

    assert applied.wait(timeout=2)
    watcher.stop()
    assert manager.snapshot.settings.requests.max_action_chars == 20

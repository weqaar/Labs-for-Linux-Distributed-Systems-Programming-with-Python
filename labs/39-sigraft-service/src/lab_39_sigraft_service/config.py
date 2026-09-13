"""Validated TOML configuration with transactional hot reload."""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Protocol

import tomli

log = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when a complete configuration candidate is invalid."""


@dataclass(frozen=True)
class RequestSettings:
    """Live-reloadable HTTP request limits."""

    max_body_bytes: int
    max_action_chars: int


@dataclass(frozen=True)
class TelemetrySettings:
    """Telemetry sampling and restart-time exporter settings."""

    sample_ratio: float
    exporter: str
    otlp_endpoint: str
    otlp_insecure: bool
    azure_connection_string: str | None


@dataclass(frozen=True)
class IdentitySettings:
    """Restart-time OpenTelemetry resource identity."""

    deployment_environment: str
    service_instance_id: str


@dataclass(frozen=True)
class AnalysisSettings:
    """Live-reloadable operational report settings."""

    enabled: bool
    observations_path: str
    refresh_interval_seconds: float


@dataclass(frozen=True)
class ServiceSettings:
    """Validated configuration used by all service subsystems."""

    schema_version: int
    identity: IdentitySettings
    requests: RequestSettings
    telemetry: TelemetrySettings
    analysis: AnalysisSettings


@dataclass(frozen=True)
class ConfigSnapshot:
    """One atomically published configuration generation."""

    settings: ServiceSettings
    revision: str
    generation: int
    restart_required: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReloadStatus:
    """Non-secret status suitable for the metadata endpoint."""

    generation: int
    revision: str
    last_result: str
    last_error: str | None
    restart_required: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return JSON-compatible public status."""

        return {
            "generation": self.generation,
            "revision": self.revision,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "restart_required": list(self.restart_required),
        }


class PreparedChange(Protocol):
    """A subsystem change prepared without changing visible state."""

    def commit(self) -> None:
        """Publish the prepared state."""

        ...

    def rollback(self) -> None:
        """Restore state from before preparation or commit."""

        ...


class ReloadSubsystem(Protocol):
    """A participant in transactional configuration reload."""

    name: str

    def prepare(self, candidate: ServiceSettings, current: ServiceSettings) -> PreparedChange:
        """Validate and stage a change without publishing it."""

        ...


_TOP_LEVEL_KEYS = {"schema", "identity", "requests", "telemetry", "analysis"}
_REQUEST_KEYS = {"max_body_bytes", "max_action_chars"}
_TELEMETRY_KEYS = {
    "sample_ratio",
    "exporter",
    "otlp_endpoint",
    "otlp_insecure",
    "azure_connection_string",
}
_ANALYSIS_KEYS = {"enabled", "observations_path", "refresh_interval_seconds"}
_RESTART_FIELDS = {
    "identity.deployment_environment",
    "identity.service_instance_id",
    "telemetry.exporter",
    "telemetry.otlp_endpoint",
    "telemetry.otlp_insecure",
    "telemetry.azure_connection_string",
}

DEFAULT_CONFIG = b"""\
[schema]
version = 1

[identity]
deployment_environment = "local"
service_instance_id = "sigraft-local"

[requests]
max_body_bytes = 65536
max_action_chars = 4096

[telemetry]
sample_ratio = 1.0
exporter = "memory"
otlp_endpoint = "http://127.0.0.1:4317"
otlp_insecure = true

[analysis]
enabled = true
observations_path = "package:observations.csv"
refresh_interval_seconds = 60.0
"""


def parse_config(content: bytes) -> tuple[ServiceSettings, str]:
    """Parse and validate a whole TOML candidate and derive its revision."""

    revision = hashlib.sha256(content).hexdigest()
    try:
        document = tomli.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomli.TOMLDecodeError) as error:
        raise ConfigurationError(f"configuration is not valid TOML: {error}") from error
    _reject_unknown("top level", document, _TOP_LEVEL_KEYS)
    schema = _table(document, "schema")
    identity = _table(document, "identity")
    requests = _table(document, "requests")
    telemetry = _table(document, "telemetry")
    analysis = _table(document, "analysis")
    _reject_unknown("schema", schema, {"version"})
    _reject_unknown("identity", identity, {"deployment_environment", "service_instance_id"})
    _reject_unknown("requests", requests, _REQUEST_KEYS)
    _reject_unknown("telemetry", telemetry, _TELEMETRY_KEYS)
    _reject_unknown("analysis", analysis, _ANALYSIS_KEYS)

    schema_version = _integer(schema, "version", minimum=1, maximum=1)
    deployment_environment = _text(identity, "deployment_environment")
    service_instance_id = _text(identity, "service_instance_id")
    max_body_bytes = _integer(requests, "max_body_bytes", minimum=256, maximum=1_048_576)
    max_action_chars = _integer(requests, "max_action_chars", minimum=1, maximum=65_536)
    sample_ratio = _number(telemetry, "sample_ratio", minimum=0.0, maximum=1.0)
    exporter = _text(telemetry, "exporter")
    if exporter not in {"memory", "otlp", "azure"}:
        raise ConfigurationError("telemetry.exporter must be memory, otlp, or azure")
    otlp_endpoint = _text(telemetry, "otlp_endpoint")
    if not otlp_endpoint.startswith(("http://", "https://")):
        raise ConfigurationError("telemetry.otlp_endpoint must be an HTTP URL")
    otlp_insecure = _boolean(telemetry, "otlp_insecure")
    connection_string = telemetry.get("azure_connection_string")
    if connection_string is not None and not isinstance(connection_string, str):
        raise ConfigurationError("telemetry.azure_connection_string must be text")
    if exporter == "azure" and not connection_string:
        raise ConfigurationError(
            "telemetry.azure_connection_string is required for the azure exporter"
        )
    analysis_enabled = _boolean(analysis, "enabled")
    observations_path = _text(analysis, "observations_path")
    refresh_interval_seconds = _number(
        analysis,
        "refresh_interval_seconds",
        minimum=5.0,
        maximum=86_400.0,
    )
    settings = ServiceSettings(
        schema_version=schema_version,
        identity=IdentitySettings(deployment_environment, service_instance_id),
        requests=RequestSettings(max_body_bytes, max_action_chars),
        telemetry=TelemetrySettings(
            sample_ratio,
            exporter,
            otlp_endpoint,
            otlp_insecure,
            connection_string,
        ),
        analysis=AnalysisSettings(
            analysis_enabled,
            observations_path,
            refresh_interval_seconds,
        ),
    )
    return settings, revision


class ConfigManager:
    """Validate, coordinate, and atomically publish configuration snapshots."""

    def __init__(self, initial: ServiceSettings, revision: str) -> None:
        self._lock = RLock()
        self._startup = initial
        self._snapshot = ConfigSnapshot(initial, revision, 1)
        self._status = ReloadStatus(1, revision, "initial", None, ())
        self._subsystems: list[ReloadSubsystem] = []

    @classmethod
    def from_bytes(cls, content: bytes) -> ConfigManager:
        """Build generation one from validated TOML."""

        settings, revision = parse_config(content)
        return cls(settings, revision)

    @property
    def snapshot(self) -> ConfigSnapshot:
        """Return the current immutable snapshot."""

        with self._lock:
            return self._snapshot

    @property
    def status(self) -> ReloadStatus:
        """Return non-secret reload status."""

        with self._lock:
            return self._status

    def register(self, subsystem: ReloadSubsystem) -> None:
        """Register a subsystem, including a later analytics refresh worker."""

        with self._lock:
            if any(existing.name == subsystem.name for existing in self._subsystems):
                raise ValueError(f"reload subsystem already registered: {subsystem.name}")
            self._subsystems.append(subsystem)

    def reload_bytes(self, content: bytes) -> bool:
        """Attempt one complete two-phase reload."""

        try:
            candidate, revision = parse_config(content)
        except ConfigurationError:
            self._record_failure("invalid configuration candidate")
            return False

        with self._lock:
            current = self._snapshot
            if revision == current.revision:
                self._status = replace(self._status, last_result="unchanged", last_error=None)
                return True
            prepared: list[PreparedChange] = []
            try:
                for subsystem in self._subsystems:
                    prepared.append(subsystem.prepare(candidate, current.settings))
            except Exception:
                rollback_failed = self._rollback(prepared)
                message = (
                    "subsystem prepare and rollback failed"
                    if rollback_failed
                    else "subsystem prepare failed"
                )
                self._record_failure(message)
                return False
            try:
                for change in prepared:
                    change.commit()
            except Exception:
                rollback_failed = self._rollback(prepared)
                message = (
                    "subsystem commit and rollback failed"
                    if rollback_failed
                    else "subsystem commit failed"
                )
                self._record_failure(message)
                return False

            restart_required = _restart_changes(self._startup, candidate)
            generation = current.generation + 1
            self._snapshot = ConfigSnapshot(candidate, revision, generation, restart_required)
            self._status = ReloadStatus(generation, revision, "applied", None, restart_required)
            return True

    def reload_file(self, path: Path) -> bool:
        """Read and reload a complete file candidate."""

        try:
            content = path.read_bytes()
        except OSError:
            self._record_failure("configuration read failed")
            return False
        return self.reload_bytes(content)

    def _record_failure(self, message: str) -> None:
        with self._lock:
            snapshot = self._snapshot
            self._status = ReloadStatus(
                snapshot.generation,
                snapshot.revision,
                "failed",
                message,
                snapshot.restart_required,
            )

    @staticmethod
    def _rollback(prepared: list[PreparedChange]) -> bool:
        failed = False
        for change in reversed(prepared):
            try:
                change.rollback()
            except Exception:
                failed = True
                log.exception("configuration subsystem rollback failed")
        return failed


@dataclass
class _ValueChange:
    """Commit and restore one lock-protected subsystem value."""

    apply: Any
    old: Any
    new: Any

    def commit(self) -> None:
        self.apply(self.new)

    def rollback(self) -> None:
        self.apply(self.old)


class RequestLimits:
    """Thread-safe request limits and their reload hook."""

    name = "request-limits"

    def __init__(self, settings: RequestSettings) -> None:
        self._lock = RLock()
        self._settings = settings

    @property
    def current(self) -> RequestSettings:
        with self._lock:
            return self._settings

    def prepare(self, candidate: ServiceSettings, current: ServiceSettings) -> PreparedChange:
        del current
        return _ValueChange(self._set, self.current, candidate.requests)

    def _set(self, settings: RequestSettings) -> None:
        with self._lock:
            self._settings = settings


class FileProbe(Protocol):
    """Return a stable token for the complete file currently on disk."""

    def token(self, path: Path) -> object:
        """Return a token that changes when the file changes."""

        ...


class FileContentProbe:
    """Detect replacement and in-place edits from file content."""

    def token(self, path: Path) -> object:
        """Return the file content digest."""

        return hashlib.sha256(path.read_bytes()).digest()


class WatcherClock(Protocol):
    """Injectable wait boundary for the watcher thread."""

    def wait(self, wake: Event, timeout: float) -> None:
        """Wait until requested or until the next poll."""

        ...


class EventWatcherClock:
    """Use a threading event for interruptible production waits."""

    def wait(self, wake: Event, timeout: float) -> None:
        wake.wait(timeout)


class ConfigWatcher:
    """Poll a configuration file and service explicit reload requests."""

    def __init__(
        self,
        manager: ConfigManager,
        path: Path,
        *,
        interval: float = 2.0,
        poll_changes: bool = True,
        probe: FileProbe | None = None,
        clock: WatcherClock | None = None,
    ) -> None:
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("watch interval must be positive")
        self._manager = manager
        self._path = path
        self._interval = interval
        self._poll_changes = poll_changes
        self._probe = probe or FileContentProbe()
        self._clock = clock or EventWatcherClock()
        self._stop = Event()
        self._wake = Event()
        self._requested = Event()
        self._thread: Thread | None = None
        try:
            self._token: object | None = self._probe.token(path)
        except OSError:
            self._token = None

    @property
    def running(self) -> bool:
        """Report whether the managed watcher thread is alive."""

        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start one managed watcher thread."""

        if self.running:
            raise RuntimeError("configuration watcher is already running")
        self._stop.clear()
        self._thread = Thread(target=self._run, name="sigraft-config", daemon=True)
        self._thread.start()

    def request_reload(self) -> None:
        """Request reload without doing file I/O in the caller or signal handler."""

        self._requested.set()
        self._wake.set()

    def poll_once(self, *, force: bool = False) -> bool:
        """Perform one deterministic file probe and reload if needed."""

        try:
            token = self._probe.token(self._path)
        except OSError:
            return self._manager.reload_file(self._path)
        if not force and token == self._token:
            return False
        applied = self._manager.reload_file(self._path)
        if applied:
            self._token = token
        return applied

    def stop(self) -> None:
        """Stop and join the watcher thread."""

        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("configuration watcher did not stop")

    def _run(self) -> None:
        while not self._stop.is_set():
            self._clock.wait(self._wake, self._interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            requested = self._requested.is_set()
            self._requested.clear()
            if requested or self._poll_changes:
                try:
                    self.poll_once(force=requested)
                except Exception:
                    log.exception("configuration watcher reload failed")
                    self._manager._record_failure("configuration watcher failed")


def _restart_changes(startup: ServiceSettings, candidate: ServiceSettings) -> tuple[str, ...]:
    changed: list[str] = []
    if startup.identity.deployment_environment != candidate.identity.deployment_environment:
        changed.append("identity.deployment_environment")
    if startup.identity.service_instance_id != candidate.identity.service_instance_id:
        changed.append("identity.service_instance_id")
    if startup.telemetry.exporter != candidate.telemetry.exporter:
        changed.append("telemetry.exporter")
    if startup.telemetry.otlp_endpoint != candidate.telemetry.otlp_endpoint:
        changed.append("telemetry.otlp_endpoint")
    if startup.telemetry.otlp_insecure != candidate.telemetry.otlp_insecure:
        changed.append("telemetry.otlp_insecure")
    if startup.telemetry.azure_connection_string != candidate.telemetry.azure_connection_string:
        changed.append("telemetry.azure_connection_string")
    return tuple(field for field in sorted(changed) if field in _RESTART_FIELDS)


def _table(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{key} must be a TOML table")
    return value


def _reject_unknown(section: str, values: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigurationError(f"unknown {section} fields: {', '.join(unknown)}")


def _integer(values: dict[str, Any], key: str, *, minimum: int, maximum: int) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{key} must be between {minimum} and {maximum}")
    return value


def _number(values: dict[str, Any], key: str, *, minimum: float, maximum: float) -> float:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ConfigurationError(f"{key} must be between {minimum} and {maximum}")
    return number


def _text(values: dict[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{key} must be non-empty text")
    return value


def _boolean(values: dict[str, Any], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise ConfigurationError(f"{key} must be true or false")
    return value

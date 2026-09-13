"""Process ownership: Uvicorn, its event-loop configuration, and process
configuration from the environment.

Nothing in this module is exercised by starting a real server in tests.
``resolve_event_loop_name`` takes its uvloop import through an injectable
seam, and ``serve`` takes its Uvicorn call through another, so the decisions
a production deployment has to make, which event loop runs, which host and
port to bind, how many workers to start, are typed and checked without
opening a socket.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol

#: The one import string every worker process, whether there is one of them
#: or many, uses to build the application. Uvicorn re-imports the
#: application in each worker process once more than one worker is running,
#: or once auto-reload is enabled, so an already-constructed application
#: object is only a valid target for a single worker with no reload; a
#: factory referenced by an import string is valid for any worker count,
#: which is why this module never branches its target on ``workers``. See
#: Uvicorn's own deployment documentation for the underlying rule.
APP_IMPORT_STRING = "lab_19_rpc_service.api:app_factory"

MIN_PORT = 1
MAX_PORT = 65535

#: An environment typo, such as an extra zero, should not be able to spawn an
#: unbounded number of worker processes. This is generous for a relay
#: deployment; a real requirement above it should override the constant, not
#: remove the check.
MAX_WORKERS = 64

#: The exact strings ``RELAY_USE_UVLOOP`` accepts, so a typo is rejected
#: loudly at startup instead of silently parsing as false.
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes"})
_FALSE_ENV_VALUES = frozenset({"0", "false", "no"})


class EventLoopImporter(Protocol):
    """Callable that imports the uvloop module, or raises ``ImportError``."""

    def __call__(self) -> Any: ...


def _default_importer() -> Any:
    return import_module("uvloop")


class UvicornRunner(Protocol):
    """The subset of ``uvicorn.run`` this module depends on."""

    def __call__(
        self,
        app_import_string: str,
        *,
        host: str,
        port: int,
        workers: int,
        loop: str,
        factory: bool,
    ) -> None: ...


def _default_runner(
    app_import_string: str,
    *,
    host: str,
    port: int,
    workers: int,
    loop: str,
    factory: bool,
) -> None:
    import uvicorn

    uvicorn.run(
        app_import_string,
        host=host,
        port=port,
        workers=workers,
        loop=loop,
        factory=factory,
    )


class UvloopUnavailableError(RuntimeError):
    """Raised when uvloop was requested explicitly and cannot be imported.

    Uvicorn's own ``loop="auto"`` falls back to asyncio silently when uvloop
    cannot be imported, which is reasonable when the operator only asked for
    whatever performs best. A configuration that names uvloop specifically is
    a different decision: silently running on asyncio instead would hide a
    missing dependency a deployment may be relying on, so this module fails
    loudly at startup instead of degrading quietly.
    """


@dataclass(frozen=True)
class ServerConfig:
    """Process-level configuration read once at startup."""

    host: str
    port: int
    workers: int
    use_uvloop: bool
    app_import_string: str = field(default=APP_IMPORT_STRING)

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host must not be empty")
        if not (MIN_PORT <= self.port <= MAX_PORT):
            raise ValueError(f"port must be between {MIN_PORT} and {MAX_PORT}")
        if self.workers <= 0:
            raise ValueError("workers must be positive")
        if self.workers > MAX_WORKERS:
            raise ValueError(f"workers must be at most {MAX_WORKERS}")
        if not self.app_import_string.strip():
            raise ValueError("app_import_string must not be empty")


def _parse_bool_env(name: str, raw: str) -> bool:
    """Parse a boolean environment variable from a fixed, documented set.

    A value outside that set is rejected rather than treated as false: a
    typo such as ``RELAY_USE_UVLOOP=tru`` should fail loudly at startup, not
    silently run on asyncio while the operator believes uvloop was
    requested.
    """

    normalized = raw.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    allowed = sorted(_TRUE_ENV_VALUES | _FALSE_ENV_VALUES)
    raise ValueError(f"{name} must be one of {allowed}, got {raw!r}")


def build_config_from_env(env: Mapping[str, str]) -> ServerConfig:
    """Build a :class:`ServerConfig` from process environment variables.

    uvloop defaults to off. It is a performance choice, not a correctness
    requirement, and a process should not have to opt out of an optional
    dependency it never asked for. Asking for it explicitly is a different
    decision from leaving the default alone: see
    :func:`resolve_event_loop_name`.
    """

    try:
        port = int(env.get("RELAY_PORT", "8000"))
    except ValueError as exc:
        raise ValueError("RELAY_PORT must be an integer") from exc
    try:
        workers = int(env.get("RELAY_WORKERS", "1"))
    except ValueError as exc:
        raise ValueError("RELAY_WORKERS must be an integer") from exc

    return ServerConfig(
        host=env.get("RELAY_HOST", "0.0.0.0"),
        port=port,
        workers=workers,
        use_uvloop=_parse_bool_env("RELAY_USE_UVLOOP", env.get("RELAY_USE_UVLOOP", "false")),
    )


def resolve_event_loop_name(
    config: ServerConfig,
    *,
    importer: EventLoopImporter = _default_importer,
) -> str:
    """Resolve the loop name for Uvicorn's own ``loop=`` setting.

    This never calls ``uvloop.install()`` directly; Uvicorn owns installing
    whichever loop it is told to run, once given back the name this function
    resolves (``"asyncio"`` or ``"uvloop"``), matching Uvicorn's own
    ``--loop`` option. When uvloop was not
    requested, the result is always ``"asyncio"`` and the importer is never
    called, so a process that never asked for uvloop is unaffected by its
    absence. When uvloop was requested explicitly and cannot be imported,
    this raises :class:`UvloopUnavailableError` rather than silently
    returning ``"asyncio"``: Uvicorn's own ``--loop auto`` behaves this way
    only for the automatic case, and an explicit request that runs on
    asyncio anyway would hide a missing dependency a deployment may depend
    on.
    """

    if not config.use_uvloop:
        return "asyncio"
    try:
        importer()
    except ImportError as exc:
        raise UvloopUnavailableError(
            "RELAY_USE_UVLOOP requested uvloop, but it is not installed. "
            "Install the optional extra, for example "
            "`pip install lab-19-rpc-service[uvloop]`, or leave "
            "RELAY_USE_UVLOOP unset to run on asyncio's default loop."
        ) from exc
    return "uvloop"


def serve(
    config: ServerConfig,
    *,
    runner: UvicornRunner = _default_runner,
    importer: EventLoopImporter = _default_importer,
) -> str:
    """Own the process: resolve the loop, then hand control to Uvicorn.

    The application is always named by :attr:`ServerConfig.app_import_string`
    and served with ``factory=True``, regardless of whether
    ``config.workers`` is one or many: Uvicorn re-imports the application in
    every worker process once more than one worker is running, so an import
    string plus factory is the one target valid for any worker count.
    Returns the event loop name that was resolved, so a caller such as a
    startup smoke test can confirm the resolution without starting a real
    server or binding a real socket.
    """

    loop_name = resolve_event_loop_name(config, importer=importer)
    runner(
        config.app_import_string,
        host=config.host,
        port=config.port,
        workers=config.workers,
        loop=loop_name,
        factory=True,
    )
    return loop_name


def main() -> None:
    """Entry point for a real process: read the environment, then serve.

    This is the one function in the module meant to run in production. It is
    intentionally thin, everything it calls is unit tested on its own, so
    that starting the process is the only thing left unverified by the local
    gate, and that gap is exactly what an integration or smoke test outside
    this checkpoint should close.
    """

    import os

    config = build_config_from_env(os.environ)
    serve(config)

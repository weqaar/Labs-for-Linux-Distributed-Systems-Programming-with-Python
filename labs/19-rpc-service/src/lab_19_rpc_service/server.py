"""Configure and start Uvicorn from environment settings.

Tests inject a uvloop importer and a Uvicorn runner to inspect loop selection,
bind address and worker count without importing uvloop or opening a socket.
Run main to start the real server.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol

#: An importable factory lets Uvicorn construct the application in each worker;
#: passing an already-built app would not support multiple workers.
APP_IMPORT_STRING = "lab_19_rpc_service.api:app_factory"

MIN_PORT = 1
MAX_PORT = 65535

#: Limit process creation to reduce the impact of a mistyped worker count.
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

    An explicit uvloop request must not silently select asyncio and hide a
    missing dependency. Leaving uvloop disabled uses asyncio without importing uvloop.
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

    Strip whitespace and ignore case. Accept 1, true and yes as true, and
    0, false and no as false. Raise ValueError for anything else so a typo
    cannot silently disable the requested loop.
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

    Read RELAY_HOST, RELAY_PORT, RELAY_WORKERS and RELAY_USE_UVLOOP from the
    supplied mapping. Defaults are 0.0.0.0, port 8000, one worker and asyncio.
    Invalid numbers, booleans or configuration limits raise ValueError.
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

    Return "asyncio" without calling importer when uvloop is disabled.
    Otherwise call importer and return "uvloop", translating ImportError
    into UvloopUnavailableError. This function does not install either loop;
    Uvicorn configures the selected loop when the server starts.
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
    """Select an event loop and call the runner with the server configuration.

    Pass the application import string with factory=True for any worker count.
    Return the loop name after the runner returns. The default runner starts
    Uvicorn and blocks until shutdown; inject a fake to inspect its arguments
    without opening a socket.
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
    """Read process settings and start Uvicorn until shutdown.

    The local tests replace the server runner. Test actual startup and HTTP
    requests separately before deploying this entry point.
    """

    import os

    config = build_config_from_env(os.environ)
    serve(config)

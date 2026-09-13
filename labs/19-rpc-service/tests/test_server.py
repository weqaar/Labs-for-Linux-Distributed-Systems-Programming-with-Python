"""Tests for the server entry point: configuration, event loop resolution and
the injectable Uvicorn seam.

Nothing here starts a real server or imports a real uvloop.
``resolve_event_loop_name`` takes its importer as a parameter precisely so
both the silent-default and the loud-failure paths can be tested whether or
not the optional ``uvloop`` extra happens to be installed.
"""

from __future__ import annotations

import pytest

from lab_19_rpc_service import (
    APP_IMPORT_STRING,
    MAX_WORKERS,
    ServerConfig,
    UvloopUnavailableError,
    build_config_from_env,
    resolve_event_loop_name,
    serve,
)


def test_build_config_from_env_uses_relay_prefixed_variables() -> None:
    config = build_config_from_env(
        {
            "RELAY_HOST": "127.0.0.1",
            "RELAY_PORT": "9000",
            "RELAY_WORKERS": "4",
            "RELAY_USE_UVLOOP": "true",
        }
    )

    assert config == ServerConfig(host="127.0.0.1", port=9000, workers=4, use_uvloop=True)


def test_build_config_from_env_defaults_uvloop_off() -> None:
    config = build_config_from_env({})

    assert config.host == "0.0.0.0"
    assert config.port == 8000
    assert config.workers == 1
    assert config.use_uvloop is False
    assert config.app_import_string == APP_IMPORT_STRING


def test_build_config_from_env_rejects_a_non_integer_port() -> None:
    with pytest.raises(ValueError, match="RELAY_PORT"):
        build_config_from_env({"RELAY_PORT": "not-a-number"})


def test_build_config_from_env_rejects_a_non_integer_worker_count() -> None:
    with pytest.raises(ValueError, match="RELAY_WORKERS"):
        build_config_from_env({"RELAY_WORKERS": "not-a-number"})


def test_build_config_from_env_accepts_the_documented_true_and_false_spellings() -> None:
    for spelling in ("1", "true", "TRUE", "yes", "Yes"):
        assert build_config_from_env({"RELAY_USE_UVLOOP": spelling}).use_uvloop is True
    for spelling in ("0", "false", "FALSE", "no", "No"):
        assert build_config_from_env({"RELAY_USE_UVLOOP": spelling}).use_uvloop is False


def test_build_config_from_env_rejects_an_unrecognised_use_uvloop_spelling() -> None:
    with pytest.raises(ValueError, match="RELAY_USE_UVLOOP"):
        build_config_from_env({"RELAY_USE_UVLOOP": "tru"})


def test_build_config_from_env_rejects_a_worker_count_above_the_bound() -> None:
    with pytest.raises(ValueError, match="workers"):
        build_config_from_env({"RELAY_WORKERS": str(MAX_WORKERS + 1)})


def test_server_config_rejects_a_port_below_the_valid_range() -> None:
    with pytest.raises(ValueError, match="port"):
        ServerConfig(host="0.0.0.0", port=0, workers=1, use_uvloop=False)


def test_server_config_rejects_a_port_above_the_valid_range() -> None:
    with pytest.raises(ValueError, match="port"):
        ServerConfig(host="0.0.0.0", port=65536, workers=1, use_uvloop=False)


def test_server_config_accepts_the_maximum_valid_port() -> None:
    config = ServerConfig(host="0.0.0.0", port=65535, workers=1, use_uvloop=False)

    assert config.port == 65535


def test_server_config_rejects_an_empty_host() -> None:
    with pytest.raises(ValueError, match="host"):
        ServerConfig(host="", port=8000, workers=1, use_uvloop=False)


def test_server_config_rejects_a_blank_host() -> None:
    with pytest.raises(ValueError, match="host"):
        ServerConfig(host="   ", port=8000, workers=1, use_uvloop=False)


def test_server_config_rejects_a_non_positive_worker_count() -> None:
    with pytest.raises(ValueError, match="workers"):
        ServerConfig(host="0.0.0.0", port=8000, workers=0, use_uvloop=False)


def test_server_config_rejects_a_worker_count_above_the_bound() -> None:
    with pytest.raises(ValueError, match="workers"):
        ServerConfig(host="0.0.0.0", port=8000, workers=MAX_WORKERS + 1, use_uvloop=False)


def test_server_config_accepts_the_maximum_worker_count() -> None:
    config = ServerConfig(host="0.0.0.0", port=8000, workers=MAX_WORKERS, use_uvloop=False)

    assert config.workers == MAX_WORKERS


def test_server_config_rejects_an_empty_app_import_string() -> None:
    with pytest.raises(ValueError, match="app_import_string"):
        ServerConfig(host="0.0.0.0", port=8000, workers=1, use_uvloop=False, app_import_string="")


def test_server_config_defaults_to_the_shared_app_import_string() -> None:
    config = ServerConfig(host="0.0.0.0", port=8000, workers=1, use_uvloop=False)

    assert config.app_import_string == APP_IMPORT_STRING


def test_server_config_uses_the_same_import_string_for_one_or_many_workers() -> None:
    single = ServerConfig(host="0.0.0.0", port=8000, workers=1, use_uvloop=False)
    many = ServerConfig(host="0.0.0.0", port=8000, workers=8, use_uvloop=False)

    assert single.app_import_string == many.app_import_string == APP_IMPORT_STRING


def test_resolve_event_loop_name_stays_on_asyncio_when_not_requested() -> None:
    config = ServerConfig(host="0.0.0.0", port=8000, workers=1, use_uvloop=False)

    def unexpected_importer() -> object:
        raise AssertionError("uvloop should not be imported when not requested")

    assert resolve_event_loop_name(config, importer=unexpected_importer) == "asyncio"


def test_resolve_event_loop_name_raises_when_uvloop_is_requested_but_missing() -> None:
    config = ServerConfig(host="0.0.0.0", port=8000, workers=1, use_uvloop=True)

    def missing_importer() -> object:
        raise ImportError("no module named uvloop")

    with pytest.raises(UvloopUnavailableError, match="RELAY_USE_UVLOOP"):
        resolve_event_loop_name(config, importer=missing_importer)


def test_resolve_event_loop_name_reports_uvloop_when_available() -> None:
    config = ServerConfig(host="0.0.0.0", port=8000, workers=1, use_uvloop=True)

    def present_importer() -> object:
        return object()

    assert resolve_event_loop_name(config, importer=present_importer) == "uvloop"


def test_serve_resolves_the_loop_then_calls_the_injected_runner_with_one_worker() -> None:
    config = ServerConfig(host="127.0.0.1", port=9000, workers=1, use_uvloop=False)
    calls = []

    def fake_runner(
        app_import_string: str,
        *,
        host: str,
        port: int,
        workers: int,
        loop: str,
        factory: bool,
    ) -> None:
        calls.append((app_import_string, host, port, workers, loop, factory))

    loop_name = serve(config, runner=fake_runner)

    assert loop_name == "asyncio"
    assert calls == [(APP_IMPORT_STRING, "127.0.0.1", 9000, 1, "asyncio", True)]


def test_serve_uses_the_same_import_string_and_factory_flag_with_many_workers() -> None:
    config = ServerConfig(host="127.0.0.1", port=9000, workers=4, use_uvloop=False)
    calls = []

    def fake_runner(
        app_import_string: str,
        *,
        host: str,
        port: int,
        workers: int,
        loop: str,
        factory: bool,
    ) -> None:
        calls.append((app_import_string, host, port, workers, loop, factory))

    serve(config, runner=fake_runner)

    assert calls == [(APP_IMPORT_STRING, "127.0.0.1", 9000, 4, "asyncio", True)]


def test_serve_propagates_a_loud_uvloop_failure_before_calling_the_runner() -> None:
    config = ServerConfig(host="127.0.0.1", port=9000, workers=1, use_uvloop=True)

    def unexpected_runner(*args: object, **kwargs: object) -> None:
        raise AssertionError("the runner should not be called when the loop cannot be resolved")

    def missing_importer() -> object:
        raise ImportError("no module named uvloop")

    with pytest.raises(UvloopUnavailableError):
        serve(config, runner=unexpected_runner, importer=missing_importer)

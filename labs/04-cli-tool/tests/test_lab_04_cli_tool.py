"""Tests for the Typer REST client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from lab_04_cli_tool.cli import Runtime, TaskClient, app
from lab_04_cli_tool.client import RelayClient, RetryPolicy
from lab_04_cli_tool.settings import DEFAULT_URL, Settings, resolve_settings

runner = CliRunner()


def runtime(client: TaskClient | None) -> Runtime:
    return Runtime(Settings("https://relay.example", "test", "token"), client)


def transport(responses: list[httpx.Response | Exception]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        response.request = request
        return response

    return httpx.MockTransport(handler)


def test_no_subcommand_is_not_success() -> None:
    result = runner.invoke(app, [])

    assert result.exit_code == 2
    assert "Usage:" in result.output


def test_settings_follow_documented_precedence(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('url = "https://file.example"\n')

    from_flag = resolve_settings(
        "https://flag.example",
        None,
        environ={},
        config_path=config,
    )
    assert from_flag.url_source == "command line"
    assert (
        resolve_settings(
            None,
            None,
            environ={"RELAY_URL": "https://env.example"},
            config_path=config,
        ).url_source
        == "environment"
    )
    from_file = resolve_settings(None, None, environ={}, config_path=config)
    from_default = resolve_settings(None, None, environ={}, config_path=tmp_path / "missing")
    assert from_file.url == "https://file.example"
    assert from_default.url == DEFAULT_URL


def test_config_does_not_reveal_the_token() -> None:
    result = runner.invoke(app, ["config"], obj=runtime(None))

    assert result.exit_code == 0
    assert "token  present" in result.stdout
    assert "token" not in result.stdout.replace("token  present", "")


def test_status_emits_machine_readable_json() -> None:
    response = httpx.Response(200, json={"state": "queued", "id": "task-17"})
    client = RelayClient(
        "https://relay.example",
        "secret",
        transport=transport([response]),
    )

    result = runner.invoke(app, ["status", "task-17", "--json"], obj=runtime(client))

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"id": "task-17", "state": "queued"}
    assert "secret" not in result.stdout


def test_not_found_has_a_distinct_exit() -> None:
    client = RelayClient(
        "https://relay.example",
        "secret",
        transport=transport([httpx.Response(404)]),
    )

    result = runner.invoke(app, ["status", "missing"], obj=runtime(client))

    assert result.exit_code == 1
    assert "task not found: missing" in result.stderr


def test_missing_credential_names_the_fix() -> None:
    settings = Settings("https://relay.example", "test", None)

    result = runner.invoke(app, ["status", "task-17"], obj=Runtime(settings, None))

    assert result.exit_code == 2
    assert "set RELAY_TOKEN or pass --token" in result.stderr


def test_timeout_and_503_back_off_then_succeed() -> None:
    delays: list[float] = []
    responses: list[httpx.Response | Exception] = [
        httpx.ConnectTimeout("connect timed out"),
        httpx.Response(503),
        httpx.Response(200, json={"id": "task-17", "state": "running"}),
    ]
    client = RelayClient(
        "https://relay.example",
        "secret",
        transport=transport(responses),
        retry=RetryPolicy(attempts=3, base_delay=0.25, max_delay=1.0),
        sleep=delays.append,
        jitter=lambda _start, _end: 0,
    )

    result = runner.invoke(app, ["status", "task-17"], obj=runtime(client))

    assert result.exit_code == 0
    assert result.stdout == "task-17  running\n"
    assert delays == [0.25, 0.5]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401), "invalid service response"),
        (httpx.Response(200, content=b"not-json"), "invalid service response"),
        (httpx.Response(200, json=["not", "an", "object"]), "expected a JSON object"),
    ],
)
def test_bad_responses_are_command_errors(response: httpx.Response, message: str) -> None:
    client = RelayClient(
        "https://relay.example",
        "secret",
        transport=transport([response]),
    )

    result = runner.invoke(app, ["status", "task-17"], obj=runtime(client))

    assert result.exit_code == 2
    assert message in result.stderr


def test_retry_policy_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="at least one"):
        RetryPolicy(attempts=0)
    with pytest.raises(ValueError, match="must not be negative"):
        RetryPolicy(base_delay=-1)


class FakeClient:
    def task(self, task_id: str) -> dict[str, Any]:
        return {"id": task_id, "state": "done"}


def test_command_accepts_a_small_fake_client() -> None:
    result = runner.invoke(app, ["status", "task-8"], obj=runtime(FakeClient()))

    assert result.exit_code == 0
    assert result.stdout == "task-8  done\n"

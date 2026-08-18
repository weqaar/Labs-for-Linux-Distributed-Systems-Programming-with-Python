"""Typer interface for relayctl."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import typer

from lab_04_cli_tool.client import RelayClient, RelayClientError, TaskNotFound
from lab_04_cli_tool.settings import Settings, resolve_settings

app = typer.Typer(no_args_is_help=True)


class TaskClient(Protocol):
    """The service operation used by this command."""

    def task(self, task_id: str) -> dict[str, Any]: ...


@dataclass
class Runtime:
    """Dependencies shared by command functions."""

    settings: Settings
    client: TaskClient | None


@app.callback()
def configure(
    context: typer.Context,
    url: Annotated[str | None, typer.Option(help="Relay service base URL")] = None,
    token: Annotated[str | None, typer.Option(envvar="RELAY_TOKEN", hidden=True)] = None,
) -> None:
    """Operate the relay task service."""
    if context.obj is not None:
        return
    settings = resolve_settings(url, token)
    client = RelayClient(settings.url, settings.token) if settings.token else None
    context.obj = Runtime(settings=settings, client=client)


@app.command("config")
def show_config(context: typer.Context) -> None:
    """Show resolved settings without revealing credentials."""
    runtime: Runtime = context.obj
    typer.echo(f"url    {runtime.settings.url} ({runtime.settings.url_source})")
    token_state = "present" if runtime.settings.token else "missing"
    typer.echo(f"token  {token_state}")


@app.command()
def status(
    context: typer.Context,
    task_id: str,
    json_output: Annotated[bool, typer.Option("--json", help="Emit stable JSON")] = False,
) -> None:
    """Fetch one task from the REST API."""
    runtime: Runtime = context.obj
    if runtime.client is None:
        typer.echo("no usable credential; set RELAY_TOKEN or pass --token", err=True)
        raise typer.Exit(2)

    try:
        task = runtime.client.task(task_id)
    except TaskNotFound as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except RelayClientError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(task, sort_keys=True, separators=(",", ":")))
    else:
        typer.echo(f"{task.get('id', task_id)}  {task.get('state', 'unknown')}")

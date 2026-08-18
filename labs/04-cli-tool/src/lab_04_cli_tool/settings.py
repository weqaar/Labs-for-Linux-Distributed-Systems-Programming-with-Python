"""Resolve relayctl configuration with visible precedence."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

DEFAULT_URL = "http://127.0.0.1:8080"


@dataclass(frozen=True)
class Settings:
    """Resolved service settings and their provenance."""

    url: str
    url_source: str
    token: str | None


def resolve_settings(
    explicit_url: str | None,
    explicit_token: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> Settings:
    """Resolve command line, environment, file and default settings."""
    environment = os.environ if environ is None else environ
    if config_path is None:
        config_path = Path("~/.config/relay/config.toml").expanduser()
    config = _read_config(config_path)

    if explicit_url:
        url, source = explicit_url, "command line"
    elif environment.get("RELAY_URL"):
        url, source = environment["RELAY_URL"], "environment"
    elif isinstance(config.get("url"), str) and config["url"]:
        url, source = config["url"], os.fspath(config_path)
    else:
        url, source = DEFAULT_URL, "built-in default"

    token = explicit_token or environment.get("RELAY_TOKEN")
    return Settings(url=url, url_source=source, token=token)


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    return value if isinstance(value, dict) else {}

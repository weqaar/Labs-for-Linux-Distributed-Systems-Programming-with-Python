# SPDX-License-Identifier: Apache-2.0
"""Build, execute and serve the documentation without external network access."""

from __future__ import annotations

import shutil
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]


def build(source: Path, output: Path, builder: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "sphinx",
            "-n",
            "-W",
            "--keep-going",
            "-b",
            builder,
            str(source),
            str(output),
        ],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


@pytest.fixture(scope="module")
def html(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("sigraft-docs")
    result = build(ROOT / "docs", output, "html")
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def test_generated_site_contains_api_and_template(html: Path) -> None:
    index = (html / "index.html").read_text()
    api = (html / "api.html").read_text()
    assert "SigRaft 0.1.0 documentation" in index
    assert "submit_task" in api and "transition_task" in api and "WebSocketClient" in api


def test_docs_can_be_served_locally(html: Path) -> None:
    handler = partial(SimpleHTTPRequestHandler, directory=str(html))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert response.status == 200
            assert b"job management" in response.read()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert not thread.is_alive()


def test_documented_examples_execute(tmp_path: Path) -> None:
    result = build(ROOT / "docs", tmp_path / "doctest", "doctest")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failures in tests" in result.stdout


@pytest.mark.parametrize(
    ("text", "builder", "diagnostic"),
    [
        ("\nMissing :ref:`nonexistent-job-reference`.\n", "html", "undefined label"),
        ("\n.. doctest::\n\n   >>> 2 + 2\n   5\n", "doctest", "Expected:"),
    ],
)
def test_documentation_gate_rejects_broken_examples(
    tmp_path: Path, text: str, builder: str, diagnostic: str
) -> None:
    source = tmp_path / "docs"
    shutil.copytree(ROOT / "docs", source, ignore=shutil.ignore_patterns("_build"))
    with (source / "index.rst").open("a") as page:
        page.write(text)
    result = build(source, tmp_path / "output", builder)
    assert result.returncode != 0
    assert diagnostic in result.stdout + result.stderr

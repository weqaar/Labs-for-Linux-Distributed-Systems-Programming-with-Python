# SPDX-License-Identifier: Apache-2.0
"""Check that documentation builds reject bad references, examples and imports."""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import pytest

ROOT = Path(__file__).resolve().parents[1]


def build_docs(source: Path, builder: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "sphinx",
            "-n",
            "-W",
            "--keep-going",
            "-E",
            "-a",
            "-b",
            builder,
            str(source),
            str(output),
        ],
        cwd=source.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.fixture
def docs_source(tmp_path: Path) -> Path:
    return Path(shutil.copytree(ROOT / "docs", tmp_path / "docs"))


@pytest.mark.parametrize("builder", ["html", "doctest"])
def test_documentation_builds(docs_source: Path, builder: str) -> None:
    result = build_docs(docs_source, builder, docs_source.parent / builder)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("defect", ["reference", "example", "import"])
def test_documentation_rejects_broken_contracts(docs_source: Path, defect: str) -> None:
    builder = "html"
    expected = "missing_relay_contract"
    if defect == "reference":
        with (docs_source / "index.rst").open("a") as stream:
            stream.write("\n:func:`missing_relay_contract`\n")
    elif defect == "example":
        builder = "doctest"
        expected = "Failed example"
        example = docs_source / "examples.rst"
        example.write_text(example.read_text().replace("'failed'", "'passed'", 1))
    else:
        expected = "autodoc import side effect"
        # Fail safely at import instead of creating a real client or starting a worker.
        (docs_source / "import_trap.py").write_text(
            "# SPDX-License-Identifier: Apache-2.0\n"
            'raise RuntimeError("autodoc import side effect")\n'
        )
        with (docs_source / "conf.py").open("a") as stream:
            stream.write(
                "\nimport sys\nfrom pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).parent))\n"
            )
        with (docs_source / "index.rst").open("a") as stream:
            stream.write("\n.. automodule:: import_trap\n")
    result = build_docs(docs_source, builder, docs_source.parent / "broken-output")
    assert result.returncode != 0, "A deliberately broken documentation build passed"
    assert expected in result.stdout + result.stderr


def test_documented_api_requires_public_docstrings() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--stdin-filename",
            "src/lab_03_quality_gate/quality.py",
            "-",
        ],
        cwd=ROOT,
        input='"""Report quality evidence."""\n\n\ndef undocumented() -> None:\n    pass\n',
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "D103" in result.stdout


def test_generated_site_is_served_on_loopback(docs_source: Path) -> None:
    output = docs_source.parent / "html"
    result = build_docs(docs_source, "html", output)
    assert result.returncode == 0, result.stdout + result.stderr
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(output))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        port = server.server_address[1]
        # Environment proxies must not send this local-only probe outside the process host.
        opener = build_opener(ProxyHandler({}))
        for page, marker in [
            ("index.html", "Missing evidence is not a passing check."),
            ("api.html", "lab_03_quality_gate.quality.run_gate"),
        ]:
            with opener.open(f"http://127.0.0.1:{port}/{page}", timeout=5) as response:
                assert response.status == 200
                assert marker in response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert not worker.is_alive()

"""Tests for the stdlib HTTP service: unit-level and one live socket test."""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http import HTTPStatus
from http.server import ThreadingHTTPServer

import pytest

from lab_35_operational_data_analysis.schema import default_dataset_path
from lab_35_operational_data_analysis.service import (
    SECURITY_HEADERS,
    AnalysisApplication,
    build_handler,
)


def test_healthz_reports_alive_without_touching_the_dataset(tmp_path) -> None:
    # A path that does not exist proves /healthz never reads the dataset.
    application = AnalysisApplication(data_path=tmp_path / "does-not-exist.csv")
    status, content_type, body = application.handle_request("GET", "/healthz")
    assert status == HTTPStatus.OK
    assert content_type == "application/json"
    assert json.loads(body) == {"status": "alive"}


def test_root_renders_the_analysis_page() -> None:
    application = AnalysisApplication(data_path=default_dataset_path())
    status, content_type, body = application.handle_request("GET", "/")
    assert status == HTTPStatus.OK
    assert content_type == "text/html; charset=utf-8"
    assert "<html" in body
    assert "Per-release summary" in body


def test_root_reports_a_missing_dataset_instead_of_raising(tmp_path) -> None:
    application = AnalysisApplication(data_path=tmp_path / "missing.csv")
    status, content_type, body = application.handle_request("GET", "/")
    assert status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert content_type == "application/json"
    assert "error" in json.loads(body)


def test_error_response_is_generic_and_never_names_the_dataset_path(tmp_path) -> None:
    missing = tmp_path / "does-not-exist-observations.csv"
    application = AnalysisApplication(data_path=missing)
    _status, _content_type, body = application.handle_request("GET", "/")
    payload = json.loads(body)
    # The error message is fixed text, not the exception's own message: an
    # OSError for a missing file embeds the absolute path it tried to open,
    # and that path must never reach the client.
    assert payload == {"error": "analysis failed; see the server log for detail"}
    assert str(missing) not in body
    assert missing.name not in body


def test_analysis_failure_is_logged_server_side(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    missing = tmp_path / "does-not-exist-observations.csv"
    application = AnalysisApplication(data_path=missing)
    with caplog.at_level(logging.ERROR, logger="lab_35_operational_data_analysis.service"):
        application.handle_request("GET", "/")
    assert "failed to build the operational analysis report" in caplog.text
    # The traceback the log captured is where the path belongs, not in the
    # response body: caplog.text is a server-side artefact, so the path is
    # expected here and only here.
    assert str(missing) in caplog.text


def test_rendered_report_does_not_expose_the_absolute_dataset_path() -> None:
    application = AnalysisApplication(data_path=default_dataset_path())
    _status, _content_type, body = application.handle_request("GET", "/")
    assert str(default_dataset_path()) not in body
    assert str(default_dataset_path().parent) not in body
    assert "observations.csv" in body


def test_unknown_route_is_not_found() -> None:
    application = AnalysisApplication(data_path=default_dataset_path())
    status, content_type, body = application.handle_request("GET", "/nope")
    assert status == HTTPStatus.NOT_FOUND
    assert content_type == "application/json"
    assert json.loads(body) == {"error": "not found"}


def test_argument_parser_binds_to_localhost_by_default() -> None:
    from lab_35_operational_data_analysis.service import build_arg_parser

    args = build_arg_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8035
    assert args.data.name == "observations.csv"


@pytest.fixture
def running_server() -> Iterator[str]:
    application = AnalysisApplication(data_path=default_dataset_path())
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(application))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_live_server_serves_the_report_page(running_server: str) -> None:
    with urllib.request.urlopen(f"{running_server}/") as response:  # noqa: S310
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        body = response.read().decode("utf-8")
        assert "<html" in body
        assert "<svg" in body


def test_live_server_serves_healthz(running_server: str) -> None:
    with urllib.request.urlopen(f"{running_server}/healthz") as response:  # noqa: S310
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"status": "alive"}


def test_live_server_report_page_carries_the_security_headers(running_server: str) -> None:
    with urllib.request.urlopen(f"{running_server}/") as response:  # noqa: S310
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value


def test_live_server_healthz_carries_the_security_headers(running_server: str) -> None:
    with urllib.request.urlopen(f"{running_server}/healthz") as response:  # noqa: S310
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value


def test_live_server_returns_404_for_an_unknown_path(running_server: str) -> None:
    request = urllib.request.Request(f"{running_server}/nope")  # noqa: S310
    try:
        urllib.request.urlopen(request)  # noqa: S310
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        pytest.fail("expected a 404 response")

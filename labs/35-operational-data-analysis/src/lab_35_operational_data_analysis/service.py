"""A small stdlib HTTP front end for the operational analysis report.

Binds to 127.0.0.1 by default, matching the local-first posture the rest of
this book uses for a checkpoint that has no reason to be reachable from
outside the machine running it. ``GET /`` renders the current analysis of
the bundled (or configured) CSV as an HTML page. ``GET /healthz`` reports
only that the process can respond, the narrower claim Chapter 34 gives
health routes: it does not prove the CSV is present or the analysis
succeeds, which is why the page route surfaces its own errors instead of
hiding them behind a healthy probe.

An analysis failure is logged in full server-side, with a traceback, and
reported to the client as a generic message with no exception text or
filesystem path in it: a stack trace or an absolute path is server-internal
detail, not something an HTTP client needs to see. Every response also
carries a small set of security headers appropriate to a page with no
scripts and no external resources.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lab_35_operational_data_analysis.pipeline import build_report
from lab_35_operational_data_analysis.schema import default_dataset_path

logger = logging.getLogger(__name__)

# The report page has no script, no external stylesheet, no external image
# and no font: the inline <style> block and the inline <svg> markup are the
# only non-text content, so default-src can be 'none' with only inline style
# allowed back in. X-Content-Type-Options stops a browser from guessing a
# different content type for the response than the one declared, and
# Referrer-Policy keeps a client from sending this page's URL onward as a
# referrer.
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@dataclass
class AnalysisApplication:
    """Builds the report page from a configured dataset path on each request.

    Rebuilding per request keeps the service simple and correct for a small,
    static, local fixture; it does not cache, so there is no staleness to
    reason about in a checkpoint this size.
    """

    data_path: Path

    def handle_request(self, method: str, path: str) -> tuple[HTTPStatus, str, str]:
        if method == "GET" and path == "/healthz":
            return HTTPStatus.OK, "application/json", json.dumps({"status": "alive"})
        if method == "GET" and path == "/":
            try:
                report = build_report(self.data_path)
            except (OSError, ValueError):
                # The full exception, including any filesystem path in it,
                # goes to the server log; the client gets a message that
                # cannot leak either the path or the exception's own text.
                logger.exception("failed to build the operational analysis report")
                body = json.dumps({"error": "analysis failed; see the server log for detail"})
                return HTTPStatus.INTERNAL_SERVER_ERROR, "application/json", body
            return HTTPStatus.OK, "text/html; charset=utf-8", report.as_html()
        return HTTPStatus.NOT_FOUND, "application/json", json.dumps({"error": "not found"})


def build_handler(application: AnalysisApplication) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to *application*."""

    class AnalysisHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            status, content_type, body = application.handle_request(self.command, self.path)
            encoded = body.encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args

    return AnalysisHandler


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser, kept separate from ``main`` so its defaults,
    including the 127.0.0.1 bind address, can be checked without starting a
    server that blocks forever.
    """
    parser = argparse.ArgumentParser(description="Serve the relay operational analysis report")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8035, type=int)
    parser.add_argument("--data", type=Path, default=default_dataset_path())
    return parser


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    args = build_arg_parser().parse_args(argv)

    application = AnalysisApplication(data_path=args.data)
    server = ThreadingHTTPServer((args.host, args.port), build_handler(application))
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""A loopback-only web app over the local database.

Reads open a short-lived connection per request. Feedback writes take the same database
lock as the CLI, so the server never holds the lock between requests.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from ..locking import database_lock
from ..storage import Repository
from .pages import LABELS, render

STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'none'"
)
MAX_BODY = 4096


def static_assets() -> dict[str, bytes]:
    folder = files("llm_updates").joinpath("web/static")
    return {
        entry.name: entry.read_bytes()
        for entry in folder.iterdir()
        if entry.is_file() and Path(entry.name).suffix in STATIC_TYPES
    }


@contextmanager
def reader(database: str):
    """Missing databases render the empty state instead of being created by a page view."""
    if not Path(database).exists():
        yield None
        return
    repo = Repository(database)
    try:
        yield repo
    finally:
        repo.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "llm-updates"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - signature from the base class
        if self.server.verbose:
            super().log_message(format, *args)

    def _host_ok(self) -> bool:
        # Rejecting unexpected Host headers blocks DNS-rebinding pages from reading the app.
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, value):
        self._send(status, json.dumps(value).encode(), "application/json")

    def _page_not_found(self):
        self._send(
            HTTPStatus.NOT_FOUND,
            b"<!doctype html><title>Not found</title><p>Not found. <a href='index.html'>Updates</a>",
            "text/html; charset=utf-8",
        )

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        path = urlsplit(self.path).path
        if path.startswith("/static/"):
            name = path.removeprefix("/static/")
            body = self.server.assets.get(name)
            if body is None:
                return self._page_not_found()
            return self._send(HTTPStatus.OK, body, STATIC_TYPES[Path(name).suffix])
        name = "index.html" if path == "/" else path.lstrip("/")
        if "/" in name:
            return self._page_not_found()
        try:
            with reader(self.server.database) as repo:
                page = render(repo, name, interactive=True)
        except ValueError as exc:
            return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc).encode(), "text/plain")
        if page is None:
            return self._page_not_found()
        self._send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request"})
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "cross-site request"})
        if urlsplit(self.path).path != "/api/feedback":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        if self.headers.get_content_type() != "application/json":
            return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected JSON"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid body size"})
        try:
            data = json.loads(self.rfile.read(length))
            entry_id, label = data["entry_id"], data["label"]
            note = data.get("note")
        except (ValueError, KeyError, TypeError):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid request"})
        if label not in {name for name, _ in LABELS} or not isinstance(entry_id, str):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid label"})
        if note is not None and not isinstance(note, str):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid note"})
        if not Path(self.server.database).exists():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "no database"})
        try:
            with database_lock(self.server.database):
                repo = Repository(self.server.database)
                try:
                    result = repo.feedback(entry_id, label, note)
                finally:
                    repo.close()
        except ValueError as exc:
            status = HTTPStatus.CONFLICT if "another process" in str(exc) else HTTPStatus.NOT_FOUND
            return self._json(status, {"error": str(exc)})
        self._json(HTTPStatus.OK, result)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, database: str, port: int, verbose=False):
        self.database = database
        self.assets = static_assets()
        self.verbose = verbose
        super().__init__(("127.0.0.1", port), Handler)


def serve(database: str, port: int, verbose=False) -> int:
    server = Server(database, port, verbose)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(json.dumps({"status": "serving", "url": url, "database": database}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

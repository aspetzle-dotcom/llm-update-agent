"""A loopback-only web app over the local database.

Reads open a short-lived connection per request. Feedback writes take the same database
lock as the CLI, so the server never holds the lock between requests.
"""

from __future__ import annotations

import json
import math
import re
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..locking import database_lock
from ..storage import Repository
from .datasets import MAX_REQUEST_BYTES, DatasetStore
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
MAX_PROJECT_BODY = 2_000_000
MAX_POINTS = 20_000
PROJECT_ID = re.compile(r"^project_[0-9a-f]{32}$")


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
        if path == "/api/datasets":
            return self._datasets_get()
        if path.startswith("/api/"):
            return self._api_get(path)
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

    def _datasets_get(self):
        try:
            dataset_id = parse_qs(urlsplit(self.path).query).get("id", [None])[0]
            store = DatasetStore(self.server.database)
            if dataset_id is None:
                return self._json(HTTPStatus.OK, store.list())
            text = store.get(dataset_id)
            if text is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Dataset not found."})
            return self._send(HTTPStatus.OK, text.encode("utf-8"), "text/csv; charset=utf-8")
        except ValueError as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _api_get(self, path):
        try:
            with reader(self.server.database) as repo:
                if path == "/api/projects":
                    return self._json(HTTPStatus.OK, repo.projects() if repo else [])
                project_id = path.removeprefix("/api/projects/")
                if PROJECT_ID.match(project_id):
                    project = repo.project(project_id) if repo else None
                    if project:
                        return self._json(HTTPStatus.OK, project)
        except ValueError as exc:
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _write_allowed(self) -> bool:
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
            return False
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request"})
            return False
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            self._json(HTTPStatus.FORBIDDEN, {"error": "cross-site request"})
            return False
        return True

    def _read_json(self, limit):
        """Returns the decoded body, or None after sending an error response."""
        if self.headers.get_content_type() != "application/json":
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "expected JSON"})
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= limit:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid body size"})
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid request"})
            return None
        return data

    def _write(self, action):
        """Run one repository write under the CLI's database lock."""
        try:
            with database_lock(self.server.database):
                repo = Repository(self.server.database)
                try:
                    return True, action(repo)
                finally:
                    repo.close()
        except ValueError as exc:
            status = HTTPStatus.CONFLICT if "another process" in str(exc) else HTTPStatus.NOT_FOUND
            self._json(status, {"error": str(exc)})
            return False, None

    def do_POST(self):
        if not self._write_allowed():
            return
        path = urlsplit(self.path).path
        if path == "/api/feedback":
            return self._feedback()
        if path == "/api/projects":
            return self._save_project()
        if path == "/api/datasets":
            return self._datasets_write()
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self):
        if not self._write_allowed():
            return
        if urlsplit(self.path).path == "/api/datasets":
            return self._datasets_write()
        project_id = urlsplit(self.path).path.removeprefix("/api/projects/")
        if not PROJECT_ID.match(project_id) or not Path(self.server.database).exists():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        ok, deleted = self._write(lambda repo: repo.delete_project(project_id))
        if ok:
            status = HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND
            self._json(status, {"deleted": deleted})

    def _feedback(self):
        data = self._read_json(MAX_BODY)
        if data is None:
            return
        entry_id, label, note = data.get("entry_id"), data.get("label"), data.get("note")
        if label not in {name for name, _ in LABELS} or not isinstance(entry_id, str):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid label"})
        if note is not None and not isinstance(note, str):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid note"})
        if not Path(self.server.database).exists():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "no database"})
        ok, result = self._write(lambda repo: repo.feedback(entry_id, label, note))
        if ok:
            self._json(HTTPStatus.OK, result)

    def _save_project(self):
        data = self._read_json(MAX_PROJECT_BODY)
        if data is None:
            return
        try:
            project = validate_project(data)
        except ValueError as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        ok, project_id = self._write(lambda repo: repo.save_project(**project))
        if ok:
            self._json(HTTPStatus.OK, {"project_id": project_id})

    def _datasets_write(self):
        store = DatasetStore(self.server.database)
        try:
            if self.command == "DELETE":
                dataset_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                store.delete(dataset_id)
                return self._json(HTTPStatus.OK, {"ok": True})
            if self.headers.get_content_type() != "application/json":
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Expected JSON."})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                return self._json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Upload too large."}
                )
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected an upload object.")
            item = store.upload(data.get("name"), data.get("csv"))
            return self._json(HTTPStatus.OK, item)
        except (ValueError, TypeError) as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_project(data: dict) -> dict:
    """Accept only the shape the dashboard sends; the series must be finite numbers."""
    project_id = data.get("project_id")
    if project_id is not None and (
        not isinstance(project_id, str) or not PROJECT_ID.match(project_id)
    ):
        raise ValueError("invalid project id")
    name = data.get("name")
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 120:
        raise ValueError("name must be 1-120 characters")
    series = data.get("data")
    if not isinstance(series, dict):
        raise ValueError("missing data")
    dates, values = series.get("dates"), series.get("values")
    if not isinstance(dates, list) or not isinstance(values, list) or len(dates) != len(values):
        raise ValueError("dates and values must be lists of equal length")
    if not 0 < len(values) <= MAX_POINTS:
        raise ValueError(f"series must have 1-{MAX_POINTS} points")
    if not all(_number(v) for v in dates) or not all(_number(v) for v in values):
        raise ValueError("dates and values must be finite numbers")
    clean = {"dates": dates, "values": values}
    for field in ("label", "freq", "source"):
        if isinstance(series.get(field), str):
            clean[field] = series[field][:200]
    settings, summary = data.get("settings", {}), data.get("summary", {})
    if not isinstance(settings, dict) or not isinstance(summary, dict):
        raise ValueError("settings and summary must be objects")
    return {
        "project_id": project_id,
        "name": name.strip(),
        "data": clean,
        "settings": settings,
        "summary": summary,
    }


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

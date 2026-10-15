import http.client
import json
import threading

import pytest
from conftest import SINCE, UNTIL

from llm_updates.coordinator import Coordinator
from llm_updates.web.pages import href, render
from llm_updates.web.server import Server
from llm_updates.web.site import build_site


@pytest.fixture
def published(repo, config, fixture_path):
    result, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path, key="web")
    return result


@pytest.fixture
def server(config, published):
    srv = Server(config.database, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_updates_page_lists_published_entries(repo, published):
    page = render(repo, "index.html", interactive=True)
    for entry in published["entries"]:
        assert entry["entry_id"] in page
    assert 'aria-current="page">Updates' in page
    assert render(repo, published["digest_id"] + ".html", interactive=True)
    assert render(repo, "digest_unknown.html", interactive=True) is None
    assert render(repo, "../etc/passwd", interactive=True) is None


def test_empty_database_shows_setup_instructions():
    page = render(None, "index.html", interactive=True)
    assert "No digests yet" in page


def test_unsafe_links_and_markup_are_neutralised():
    assert href("javascript:alert(1)") == "#"
    assert href("https://example.org/a?b=1&c=2") == "https://example.org/a?b=1&amp;c=2"
    from llm_updates.web.pages import entry_card

    card = entry_card(
        1, {"title": "<script>x</script>", "url": "data:text/html,hi"}, "baseline", None, False
    )
    assert "<script>x" not in card and 'href="#"' in card


def test_server_serves_pages_and_assets(server):
    status, body = request(server, "GET", "/")
    assert status == 200 and b"What's new in LLMs" in body
    for path in ("/classical-ml.html", "/deep-learning.html", "/llms.html", "/static/viz.js"):
        assert request(server, "GET", path)[0] == 200
    assert request(server, "GET", "/static/../pages.py")[0] == 404
    assert request(server, "GET", "/", headers={"Host": "evil.example"})[0] == 403


def test_feedback_round_trip_and_guards(server, repo, published):
    entry = published["entries"][0]["entry_id"]
    body = json.dumps({"entry_id": entry, "label": "useful"})
    json_headers = {"Content-Type": "application/json"}
    status, data = request(server, "POST", "/api/feedback", body, json_headers)
    assert status == 200 and json.loads(data)["label"] == "useful"
    assert repo.one("SELECT label FROM feedback WHERE entry_id=?", (entry,))["label"] == "useful"
    _, page = request(server, "GET", "/")
    assert b'data-label="useful" aria-pressed="true"' in page

    cross = {**json_headers, "Origin": "https://evil.example"}
    assert request(server, "POST", "/api/feedback", body, cross)[0] == 403
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    assert request(server, "POST", "/api/feedback", body, form)[0] == 415
    bad = json.dumps({"entry_id": entry, "label": "mastered"})
    assert request(server, "POST", "/api/feedback", bad, json_headers)[0] == 400
    missing = json.dumps({"entry_id": "entry_missing", "label": "skip"})
    assert request(server, "POST", "/api/feedback", missing, json_headers)[0] == 404


def test_static_site_is_read_only(config, published, tmp_path):
    result = build_site(config.database, tmp_path / "site")
    out = tmp_path / "site"
    index = (out / "index.html").read_text(encoding="utf-8")
    assert result["files"] >= 8
    assert 'data-mode="static"' in index and "data-label=" not in index
    assert (out / (published["digest_id"] + ".html")).exists()
    assert (out / "static" / "style.css").exists() and (out / ".nojekyll").exists()


def test_static_site_without_database(tmp_path):
    build_site(str(tmp_path / "missing.sqlite3"), tmp_path / "site")
    assert not (tmp_path / "missing.sqlite3").exists()
    assert "No digests yet" in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")

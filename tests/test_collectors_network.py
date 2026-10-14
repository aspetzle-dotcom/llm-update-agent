import json
import socket
import time

import pytest
from conftest import SINCE, UNTIL

from llm_updates.collectors import arxiv_records, github_records, lab_record, pages
from llm_updates.models import CollectorResult
from llm_updates.network import Fetcher, FetchError, Response, approved, public_addresses


def release(i):
    return {
        "id": i,
        "name": f"Release {i}",
        "tag_name": f"v{i}",
        "draft": False,
        "prerelease": False,
        "body": "A retrieval evaluation change.",
        "html_url": f"https://github.com/vllm-project/vllm/releases/tag/v{i}",
        "published_at": "2026-10-12T00:00:00Z",
    }


def test_github_identity_prerelease_and_changed_notes(config):
    source = config.sources[2]
    first = release(9)
    pre = {**release(10), "prerelease": True}
    items, count = github_records(json.dumps([first, pre]), source)
    assert count == 2 and len(items) == 1
    assert items[0].canonical_key == "github:vllm-project/vllm:9"
    newer = {
        **first,
        "name": "Renamed tag",
        "tag_name": "new-tag",
        "body": "A different retrieval evaluation baseline.",
    }
    second, _ = github_records(json.dumps([newer]), source)
    assert second[0].canonical_key == items[0].canonical_key
    assert second[0].content_text != items[0].content_text


def test_arxiv_parser_cross_listing_and_malformed(config):
    body = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
    xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"><opensearch:totalResults>1</opensearch:totalResults>
    <entry><id>http://arxiv.org/abs/2610.12345v2</id><title>Context selection</title>
    <summary>A retrieval abstract.</summary><published>2026-10-12T00:00:00Z</published>
    <updated>2026-10-13T00:00:00Z</updated></entry></feed>"""
    cl, total = arxiv_records(body, config.sources[0])
    lg, _ = arxiv_records(body, config.sources[1])
    assert cl[0].canonical_key == lg[0].canonical_key == "arxiv:2610.12345"
    assert cl[0].publisher_version == "v2" and total == 1
    with pytest.raises(FetchError):
        arxiv_records(b"<feed><broken", config.sources[0])


def test_html_dates_scripts_and_selector_failure(config):
    source = config.sources[-1]
    raw = b"""<html><h1>Retrieval note</h1><script type="application/ld+json">
    {"@type":"Article","datePublished":"2026-10-11","dateModified":"2026-10-12"}</script>
    <article>Retrieval selects passages.<script>steal_keys()</script><nav>Menu</nav></article></html>"""
    item = lab_record(raw, "https://www.anthropic.com/engineering/test", source)
    assert item.published_at.startswith("2026-10-11")
    assert "steal_keys" not in item.content_text and "Menu" not in item.content_text
    with pytest.raises(FetchError, match="selector"):
        lab_record(b"<h1>Missing article</h1>", item.canonical_url, source)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/repos/vllm-project/vllm/releases",
        "https://api.github.com:8443/repos/vllm-project/vllm/releases",
        "https://user:secret@api.github.com/repos/vllm-project/vllm/releases",
        "https://127.0.0.1/repos/vllm-project/vllm/releases",
        "https://[::1]/repos/vllm-project/vllm/releases",
        "https://api.github.com.evil.test/repos/vllm-project/vllm/releases",
        "https://api.github.com/repos/vllm-project/vllm/releases/../secrets",
        "https://api.github.com/repos/vllm-project/vllm/releases/%252e%252e/secrets",
        "https://api.github.com/repos/other/repo/releases",
    ],
)
def test_network_destination_denial(config, url):
    with pytest.raises(FetchError):
        approved(url, config.sources[2])


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.1.1.1", "169.254.169.254", "::1", "fc00::1", "ff02::1"]
)
def test_private_dns_rejected(monkeypatch, ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]
    )
    with pytest.raises(FetchError, match="non_public_dns"):
        public_addresses("api.github.com")


class ScriptedFetcher(Fetcher):
    def __init__(self, config, repo, responses):
        super().__init__(config, repo, time.monotonic() + 30, sleep=lambda _: None)
        self.responses = iter(responses)
        self.urls = []

    def once(self, url, source, headers):
        self.urls.append(url)
        approved(url, source)
        return next(self.responses)


def test_conditional_request_reuses_body(repo, config):
    s = config.sources[2]
    one = ScriptedFetcher(config, repo, [Response(200, {"etag": '"test"'}, b"[]", s.endpoint)])
    one.get(s.endpoint, s)
    two = ScriptedFetcher(config, repo, [Response(304, {}, b"", s.endpoint)])
    assert two.get(s.endpoint, s).body == b"[]"


def test_redirect_private_destination_and_rate_limit(repo, config):
    s = config.sources[2]
    redirect = ScriptedFetcher(
        config, repo, [Response(302, {"location": "https://127.0.0.1/"}, b"", s.endpoint)]
    )
    with pytest.raises(FetchError, match="destination_not_registered"):
        redirect.get(s.endpoint, s)
    limited = ScriptedFetcher(
        config, repo, [Response(429, {"retry-after": "1000"}, b"", s.endpoint)]
    )
    with pytest.raises(FetchError, match="retry_exceeds_deadline"):
        limited.get(s.endpoint, s)
    assert repo.one("SELECT next_allowed_at FROM sources WHERE source_id=?", (s.source_id,))[
        "next_allowed_at"
    ]


def test_three_total_attempts(repo, config):
    s = config.sources[2]
    fetcher = ScriptedFetcher(config, repo, [Response(500, {}, b"", s.endpoint)] * 3)
    with pytest.raises(FetchError, match="server_error"):
        fetcher.get(s.endpoint, s)
    assert len(fetcher.urls) == 3


def test_pagination_resume_does_not_skip_page(repo, config):
    source = config.sources[2]
    run = repo.start(config, {})
    next_url = source.endpoint + "?page=2"

    class Pages:
        def __init__(self):
            self.urls = []

        def get(self, url, source):
            self.urls.append(url)
            if "page=2" in url:
                return Response(200, {}, json.dumps([release(2)]).encode(), url)
            return Response(
                200, {"link": f'<{next_url}>; rel="next"'}, json.dumps([release(1)]).encode(), url
            )

    fetcher = Pages()
    first, final = next(pages(source, fetcher, SINCE, UNTIL, 100))
    assert not final
    repo.commit_page(run["run_id"], source.source_id, first, final)
    resumed = list(pages(source, fetcher, SINCE, UNTIL, 100, first.cursor, 1))
    assert resumed[0][0].items[0].external_id == "2"
    assert fetcher.urls[-1] == source.endpoint + "?per_page=10&page=2"


def test_shared_arxiv_limiter_survives_page_commit(repo, config, monkeypatch):
    run = repo.start(config, {})
    f = Fetcher(config, repo, time.monotonic() + 30)
    f.throttle(config.sources[0])
    repo.commit_page(run["run_id"], config.sources[0].source_id, CollectorResult(items=[]), True)
    waits = []
    monkeypatch.setattr(f, "wait", waits.append)
    f.throttle(config.sources[1])
    assert waits and waits[0] > 0


def test_pinned_connection_keeps_tls_hostname(monkeypatch):
    from llm_updates.network import PinnedHTTPSConnection

    calls = []

    class Raw:
        def settimeout(self, value):
            pass

        def connect(self, address):
            calls.append(address)

        def close(self):
            pass

    class TLS:
        def wrap_socket(self, sock, server_hostname):
            calls.append(server_hostname)
            return sock

    monkeypatch.setattr(socket, "socket", lambda *args: Raw())
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("second lookup")),
    )
    c = PinnedHTTPSConnection(
        "api.github.com", (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.5", 443)), 10
    )
    c._context = TLS()
    c.connect()
    assert calls == [("140.82.112.5", 443), "api.github.com"]

"""HTTPS source transport. DNS addresses are validated and used for the actual socket."""

from __future__ import annotations

import http.client
import ipaddress
import json
import queue
import random
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote, urljoin, urlsplit

from .config import Config, Source
from .util import date, dumps, now


class FetchError(Exception):
    def __init__(self, code: str, retry_at: str | None = None):
        super().__init__(code)
        self.code, self.retry_at = code, retry_at


def approved(url: str, source: Source):
    try:
        p = urlsplit(url)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.port not in (None, 443)
            or p.username is not None
            or p.password is not None
            or p.fragment
        ):
            raise ValueError
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            pass
        else:
            raise ValueError
        if any(ord(c) < 33 for c in url) or "\\" in url:
            raise ValueError
        path = unquote(p.path)
        if any(c in path for c in ("%", "\\", "?", "#")) or any(
            part in (".", "..") for part in path.split("/")
        ):
            raise ValueError
        paths = source.allowed_paths.get(p.hostname.lower(), [])
        if not any(
            path == base or (base.endswith("/") and path.startswith(base)) for base in paths
        ):
            raise ValueError
        return p
    except (ValueError, TypeError):
        raise FetchError("destination_not_registered") from None


def public_addresses(host: str, timeout: float = 10):
    results = queue.Queue()

    def resolve():
        try:
            results.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception as exc:
            results.put(exc)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = results.get(timeout=timeout)
    except queue.Empty:
        raise FetchError("dns_timeout") from None
    if isinstance(addresses, Exception):
        raise FetchError("dns_failure") from None
    if not addresses:
        raise FetchError("empty_dns")
    for family, _, _, _, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if (
            family not in (socket.AF_INET, socket.AF_INET6)
            or not ip.is_global
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_loopback
            or ip.is_link_local
            or (
                isinstance(ip, ipaddress.IPv6Address)
                and (
                    ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None
                )
            )
        ):
            raise FetchError("non_public_dns")
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        family, socktype, proto, _, sockaddr = self.address
        raw = socket.socket(family, socktype, proto)
        raw.settimeout(self.timeout)
        try:
            raw.connect(sockaddr)  # No second hostname lookup; TLS still verifies the hostname.
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str


class Fetcher:
    def __init__(
        self,
        config: Config,
        repo,
        deadline: float,
        *,
        resolver=public_addresses,
        connection=PinnedHTTPSConnection,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.config, self.repo, self.deadline = config, repo, deadline
        self.resolver, self.connection, self.sleep, self.clock = resolver, connection, sleep, clock

    def remaining(self):
        value = self.deadline - self.clock()
        if value <= 0:
            raise FetchError("deadline_exceeded")
        return value

    def wait(self, seconds):
        if seconds >= self.remaining():
            raise FetchError(
                "retry_exceeds_deadline",
                datetime.fromtimestamp(time.time() + seconds, UTC).isoformat(),
            )
        if seconds > 0:
            self.sleep(seconds)

    def throttle(self, source):
        if source.kind == "arxiv":
            row = self.repo.one(
                "SELECT MAX(next_allowed_at) AS next FROM sources WHERE kind='arxiv'"
            )
        else:
            row = self.repo.one(
                "SELECT next_allowed_at AS next FROM sources WHERE source_id=?", (source.source_id,)
            )
        if row and row["next"]:
            self.wait(max(0, datetime.fromisoformat(row["next"]).timestamp() - time.time()))
        if source.kind == "arxiv":
            with self.repo.db:
                self.repo.db.execute(
                    "UPDATE sources SET next_allowed_at=? WHERE kind='arxiv'",
                    (datetime.fromtimestamp(time.time() + 3.1, UTC).isoformat(),),
                )

    def once(self, url, source, headers):
        p = approved(url, source)
        self.throttle(source)
        timeout = min(10, self.remaining())
        addresses = self.resolver(p.hostname, timeout)
        # Defense in depth also validates injected resolvers in tests/custom integrations.
        if not all(ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise FetchError("non_public_dns")
        if not addresses:
            raise FetchError("empty_dns")
        conn = self.connection(p.hostname, addresses[0], min(15, self.remaining()))
        live_socket = [None]

        def interrupt_socket():
            sock = conn.sock or live_socket[0]
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        watchdog = threading.Timer(self.remaining(), interrupt_socket)
        watchdog.daemon = True
        watchdog.start()
        try:
            conn.request("GET", p.path + ("?" + p.query if p.query else ""), headers=headers)
            live_socket[0] = conn.sock
            response = conn.getresponse()
            result_headers = {k.lower(): v for k, v in response.getheaders()}
            if result_headers.get("content-encoding", "identity").lower() not in ("identity", ""):
                raise FetchError("unexpected_content_encoding")
            body = bytearray()
            while True:
                self.remaining()
                if conn.sock:
                    conn.sock.settimeout(min(15, self.remaining()))
                chunk = response.read1(
                    min(65536, self.config.limits.response_bytes + 1 - len(body))
                )
                body.extend(chunk)
                if len(body) > self.config.limits.response_bytes:
                    raise FetchError("response_too_large")
                if not chunk:
                    break
            return Response(response.status, result_headers, bytes(body), url)
        finally:
            watchdog.cancel()
            conn.close()

    def get(self, url: str, source: Source) -> Response:
        approved(url, source)
        original = url
        cached = self.repo.one("SELECT * FROM http_cache WHERE url=?", (original,))
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept-Encoding": "identity",
            "Accept": "application/atom+xml,application/rss+xml,application/json,text/html",
        }
        if cached:
            if cached["etag"]:
                headers["If-None-Match"] = cached["etag"]
            if cached["modified"]:
                headers["If-Modified-Since"] = cached["modified"]
        for attempt in range(3):
            current = original
            try:
                for hop in range(4):
                    response = self.once(current, source, headers)
                    if response.status not in (301, 302, 303, 307, 308):
                        break
                    if hop == 3 or "location" not in response.headers:
                        raise FetchError("redirect_limit")
                    current = urljoin(current, response.headers["location"])
                    approved(current, source)
                if response.status == 304:
                    if not cached:
                        raise FetchError("304_without_cache")
                    return Response(
                        200, json.loads(cached["headers_json"]), cached["body"], current
                    )
                limited = response.status == 429 or (
                    response.status == 403
                    and (
                        "retry-after" in response.headers
                        or response.headers.get("x-ratelimit-remaining") == "0"
                    )
                )
                if limited or response.status >= 500:
                    delay = (2**attempt) + random.random()
                    if limited:
                        retry = response.headers.get("retry-after", "60")
                        delay = (
                            float(retry)
                            if retry.isdigit()
                            else max(
                                0,
                                datetime.fromisoformat(date(retry) or now()).timestamp()
                                - time.time(),
                            )
                        )
                        reset = response.headers.get("x-ratelimit-reset")
                        if reset and reset.isdigit():
                            delay = max(delay, int(reset) - time.time())
                        retry_at = datetime.fromtimestamp(time.time() + delay, UTC).isoformat()
                        with self.repo.db:
                            self.repo.db.execute(
                                "UPDATE sources SET next_allowed_at=? WHERE source_id=?",
                                (retry_at, source.source_id),
                            )
                    if attempt == 2:
                        raise FetchError(
                            "rate_limited" if limited else "server_error",
                            retry_at if limited else None,
                        )
                    self.wait(delay)
                    continue
                if response.status != 200:
                    raise FetchError(f"http_{response.status}")
                with self.repo.db:
                    self.repo.db.execute(
                        "INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?,?,?)",
                        (
                            original,
                            response.headers.get("etag"),
                            response.headers.get("last-modified"),
                            response.body,
                            dumps(response.headers),
                            now(),
                        ),
                    )
                return response
            except (TimeoutError, OSError, http.client.HTTPException):
                if attempt == 2:
                    raise FetchError("transport_failure") from None
                self.wait(2**attempt + random.random())
        raise FetchError("attempts_exhausted")

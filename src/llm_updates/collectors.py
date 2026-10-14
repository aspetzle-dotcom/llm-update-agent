from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup

from .models import CollectorResult, NormalizedItem
from .network import FetchError, approved
from .util import arxiv_id, canonical_url, clean, date, dumps, now, sha


def text_html(value):
    soup = BeautifulSoup(value, "html.parser")
    for element in soup.select("script,style,nav,header,footer,noscript,form"):
        element.decompose()
    return clean(soup.get_text(" ", strip=True))


def arxiv_records(body, source):
    feed = feedparser.parse(body)
    if feed.bozo or not feed.version:
        raise FetchError("malformed_feed")
    items = []
    for entry in feed.entries:
        base, version = arxiv_id(entry.get("id", entry.get("link", "")))
        raw_date = entry.get("published")
        raw_update = entry.get("updated")
        items.append(
            NormalizedItem(
                source_id=source.source_id,
                external_id=base,
                canonical_key="arxiv:" + base,
                canonical_url="https://arxiv.org/abs/" + base,
                title=entry.get("title", ""),
                kind="arxiv",
                fetched_at=now(),
                content_text=text_html(entry.get("summary", "")),
                content_scope="abstract-only",
                raw_hash=sha(body),
                published_at=date(raw_date),
                updated_at=date(raw_update),
                publisher_version=version,
                raw_published_at=raw_date,
                raw_updated_at=raw_update,
                authors=[a.get("name", "") for a in entry.get("authors", [])],
            )
        )
    return items, int(feed.feed.get("opensearch_totalresults", len(items)))


def github_records(body, source):
    releases = json.loads(body)
    if not isinstance(releases, list):
        raise FetchError("github_schema_changed")
    items = []
    for entry in releases:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), int):
            raise FetchError("github_schema_changed")
        if entry.get("draft") or (source.skip_prereleases and entry.get("prerelease")):
            continue
        release_id = str(entry["id"])
        body_text = text_html(entry.get("body") or "")
        if not body_text:
            continue
        items.append(
            NormalizedItem(
                source_id=source.source_id,
                external_id=release_id,
                canonical_key=f"github:{source.repository.lower()}:{release_id}",
                canonical_url=entry["html_url"],
                title=entry.get("name") or entry.get("tag_name", ""),
                kind="github",
                fetched_at=now(),
                content_text=body_text,
                content_scope="release-notes-only",
                raw_hash=sha(dumps(entry)),
                published_at=date(entry.get("published_at")),
                updated_at=date(entry.get("updated_at")),
                raw_published_at=entry.get("published_at"),
                raw_updated_at=entry.get("updated_at"),
                publisher_version=entry.get("tag_name"),
            )
        )
    return items, len(releases)


def lab_record(body, url, source):
    soup = BeautifulSoup(body, "html.parser")
    canonical = soup.select_one('link[rel="canonical"][href]')
    target = canonical_url(urljoin(url, canonical["href"])) if canonical else canonical_url(url)
    approved(target, source)
    article = soup.select_one(source.article_selector)
    title = soup.select_one("h1")
    if not article or not title:
        raise FetchError("article_selector_changed")
    raw_published, raw_updated = None, None
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
            candidates = data if isinstance(data, list) else [data]
            for node in list(candidates):
                if isinstance(node, dict):
                    candidates.extend(node.get("@graph", []))
            for node in candidates:
                if isinstance(node, dict) and node.get("datePublished"):
                    raw_published, raw_updated = node["datePublished"], node.get("dateModified")
                    break
        except (ValueError, TypeError):
            continue
    time_tag = soup.select_one("time[datetime]")
    meta = soup.select_one('meta[property="article:published_time"]')
    raw_published = raw_published or (meta.get("content") if meta else None)
    raw_published = raw_published or (time_tag.get("datetime") if time_tag else None)
    return NormalizedItem(
        source_id=source.source_id,
        external_id=target,
        canonical_key="lab:" + target,
        canonical_url=target,
        title=title.get_text(" ", strip=True),
        kind="lab",
        fetched_at=now(),
        content_text=text_html(str(article)),
        content_scope="article-body",
        raw_hash=sha(body),
        published_at=date(raw_published),
        updated_at=date(raw_updated),
        raw_published_at=raw_published,
        raw_updated_at=raw_updated,
    )


def pages(source, fetcher, since, until, cap, cursor=None, already=0):
    """Yield page plus terminal flag. Every yield is committed before the next fetch."""
    state = json.loads(cursor) if cursor else {}
    if source.kind == "arxiv":
        start = state.get("start", 0)
        seen_hashes = state.get("hashes", [])
        while already < cap:
            p = urlsplit(source.endpoint)
            if p.path != "/api/query":
                response = fetcher.get(source.endpoint, source)
                items, _ = arxiv_records(response.body, source)
                yield (
                    CollectorResult(
                        items=items[:cap],
                        complete=False,
                        warnings=["RSS is a rolling snapshot; requested window is not guaranteed"],
                    ),
                    True,
                )
                return
            query = {k: v[-1] for k, v in parse_qs(p.query).items()}

            # arXiv's date range is inclusive; application eligibility enforces [since,until).
            def stamp(d):
                return datetime.fromisoformat(d).strftime("%Y%m%d%H%M")

            query.update(
                search_query=f"cat:{source.category} AND submittedDate:[{stamp(since)} TO {stamp(until)}]",
                start=str(start),
                max_results=str(min(50, cap - already)),
                sortBy="submittedDate",
                sortOrder="descending",
            )
            response = fetcher.get(
                urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), "")), source
            )
            if sha(response.body) in seen_hashes:
                raise FetchError("repeated_page")
            seen_hashes.append(sha(response.body))
            items, total = arxiv_records(response.body, source)
            start += len(items)
            already += len(items)
            finished = start >= total
            stalled = not items and not finished
            capped = already >= cap and not finished
            yield (
                CollectorResult(
                    items=items,
                    cursor=dumps({"start": start, "hashes": seen_hashes}),
                    complete=finished,
                    warnings=["candidate cap reached"]
                    if capped
                    else (["empty page before declared total"] if stalled else []),
                ),
                finished or capped or stalled,
            )
            if finished or capped or stalled:
                return
    elif source.kind == "github":
        already = state.get("seen_count", already)
        page_size = min(10, cap)
        url = state.get("next", source.endpoint + f"?per_page={page_size}")
        seen = set(state.get("seen", []))
        while already < cap:
            if url in seen:
                raise FetchError("pagination_cycle")
            seen.add(url)
            response = fetcher.get(url, source)
            items, fetched = github_records(response.body, source)
            items = items[: max(0, cap - already)]
            already += fetched
            match = re.search(r'<([^>]+)>;\s*rel="next"', response.headers.get("link", ""))
            next_url = None
            if match:
                # GitHub returns numeric /repositories/ID aliases. Only consume a page number;
                # rebuild the URL from the configured endpoint so links cannot widen permissions.
                link = urlsplit(match[1])
                page = parse_qs(link.query).get("page", [""])[0]
                current_page = parse_qs(urlsplit(url).query).get("page", ["1"])[0]
                if (
                    link.scheme != "https"
                    or link.hostname != "api.github.com"
                    or link.username
                    or link.password
                    or link.port not in (None, 443)
                    or not page.isdigit()
                    or int(page) != int(current_page) + 1
                ):
                    raise FetchError("invalid_pagination_link")
                next_url = (
                    source.endpoint + "?" + urlencode({"per_page": page_size, "page": int(page)})
                )
            # Release order is not a publication-time guarantee. Exhaust pagination or report cap.
            finished = next_url is None
            capped = already >= cap and not finished
            yield (
                CollectorResult(
                    items=items,
                    cursor=dumps({"next": next_url, "seen": sorted(seen), "seen_count": already}),
                    complete=finished,
                    warnings=["candidate cap reached"] if capped else [],
                ),
                finished or capped,
            )
            if finished or capped:
                return
            url = next_url
    else:
        links = state.get("links")
        if links is None:
            response = fetcher.get(source.endpoint, source)
            soup = BeautifulSoup(response.body, "html.parser")
            links = []
            for anchor in soup.select(source.index_selector):
                url = canonical_url(urljoin(source.endpoint, anchor.get("href", "")))
                p = urlsplit(url)
                if not p.path.startswith(source.article_path) or url == source.endpoint.rstrip("/"):
                    continue
                try:
                    approved(url, source)
                except FetchError:
                    continue
                if url not in links:
                    links.append(url)
            if not links:
                raise FetchError("index_selector_changed")
        offset = state.get("offset", 0)
        truncated = len(links) > cap
        while offset < min(len(links), cap):
            url = links[offset]
            response = fetcher.get(url, source)
            item = lab_record(response.body, url, source)
            offset += 1
            final = offset >= min(len(links), cap)
            warnings = ["Official index snapshot only; full window coverage is not guaranteed"]
            if truncated:
                warnings.append("candidate cap reached")
            yield (
                CollectorResult(
                    items=[item],
                    cursor=dumps({"links": links, "offset": offset}),
                    complete=False,
                    warnings=warnings,
                ),
                final,
            )

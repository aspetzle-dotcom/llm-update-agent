from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    return dt.replace(tzinfo=dt.tzinfo or UTC).astimezone(UTC).isoformat(timespec="seconds")


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha(value: str | bytes | object) -> str:
    if not isinstance(value, (str, bytes)):
        value = dumps(value)
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def uid(prefix: str, *values: object) -> str:
    return prefix + "_" + sha(values)[:32]


def clean(value: str) -> str:
    # Remove terminal control/bidi characters before storage; evidence spans use this text.
    value = "".join(
        c
        for c in unicodedata.normalize("NFC", value)
        if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf")
    )
    return "\n".join(" ".join(line.split()) for line in value.splitlines()).strip()


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        raise ValueError("invalid public source URL")
    if p.port not in (None, 80, 443):
        raise ValueError("unexpected source URL port")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    return urlunsplit(("https", p.hostname.lower(), p.path or "/", urlencode(query), ""))


def arxiv_id(value: str) -> tuple[str, str | None]:
    value = re.sub(r"^.*(?:/abs/|arXiv:)", "", value)
    match = re.fullmatch(r"(\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(v\d+)?", value)
    if not match:
        raise ValueError("invalid arXiv identifier")
    return match[1], match[2]

"""HTML pages shared by the local web app and the static site export.

Pages use flat, relative file names so the same markup works from the local server
and from any subdirectory a static host serves.
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from importlib.resources import files
from urllib.parse import urlsplit

from ..util import clean

PAGES = (
    ("index.html", "Updates"),
    ("datasets.html", "Datasets & tools"),
    ("forecasting.html", "Forecasting"),
    ("classical-ml.html", "Classical ML"),
    ("deep-learning.html", "Deep learning"),
    ("llms.html", "LLMs"),
)
LEARN = {
    "classical-ml.html": ("Classical ML", "classical-ml"),
    "deep-learning.html": ("Deep learning", "deep-learning"),
    "llms.html": ("LLMs", "llms"),
    "forecasting.html": ("Forecasting", "forecasting"),
}
SCRIPTS = {"forecasting.html": ("forecast.js", "dashboard.js")}
LABELS = (("useful", "Useful"), ("already_knew", "Already knew"), ("skip", "Skip"))
DIGEST_ID = re.compile(r"^digest_[0-9a-f]{8,64}$")


def esc(value) -> str:
    return html.escape(clean("" if value is None else str(value)))


def href(url) -> str:
    """Only web URLs become links; stored source text never yields a script or data link."""
    try:
        scheme = urlsplit(str(url)).scheme.lower()
    except ValueError:
        return "#"
    return esc(url) if scheme in ("http", "https") else "#"


def digest_page(digest_id: str) -> str:
    return f"{digest_id}.html"


def _parse(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def nice_date(value, end_exclusive=False) -> str:
    dt = _parse(value)
    if not dt:
        return esc(value or "unknown date")
    if end_exclusive:
        dt -= timedelta(microseconds=1)
    return f"{dt.day} {dt:%b %Y}"


def window_label(window) -> str:
    since = _parse(window.get("since"))
    until = _parse(window.get("until"))
    if not since or not until:
        return "unknown window"
    last = until - timedelta(microseconds=1)
    start = f"{since.day} {since:%b}" + ("" if since.year == last.year else f" {since:%Y}")
    return f"{start} – {last.day} {last:%b %Y}"


# Data access -----------------------------------------------------------------------------


def digest_summaries(repo) -> list[dict]:
    if repo is None:
        return []
    rows = repo.all(
        "SELECT digest_id,run_id,status,created_at,payload_json FROM digests "
        "ORDER BY created_at DESC, rowid DESC"
    )
    result = []
    for row in rows:
        payload = json.loads(row.pop("payload_json"))
        row.update(
            mode=payload.get("mode", ""),
            window=payload.get("window", {}),
            count=len(payload.get("entries", [])),
            topics=[t for e in payload.get("entries", []) for t in e.get("topics", [])],
        )
        result.append(row)
    return result


def latest_labels(repo) -> dict[str, str]:
    """Only the latest label per entry governs personalization, so show that one."""
    if repo is None:
        return {}
    rows = repo.all(
        """SELECT f.entry_id,f.label FROM feedback f
        WHERE f.rowid=(SELECT MAX(f2.rowid) FROM feedback f2 WHERE f2.entry_id=f.entry_id)"""
    )
    return {r["entry_id"]: r["label"] for r in rows}


# Layout ----------------------------------------------------------------------------------


def layout(title: str, active: str, body: str, interactive: bool, scripts=()) -> str:
    links = []
    for name, label in PAGES:
        current = ' aria-current="page"' if name == active else ""
        links.append(f'<a href="{name}"{current}>{label}</a>')
    extra = "".join(f'<script src="static/{s}" defer></script>' for s in scripts)
    mode = "live" if interactive else "static"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Fieldnotes</title>
<link rel="stylesheet" href="static/style.css">
<link rel="stylesheet" href="static/fieldnotes.css">
<script src="static/theme.js"></script>
</head>
<body data-mode="{mode}">
<aside class="workspace-nav">
  <a class="fieldnotes-brand" href="index.html"><span>▧</span> fieldnotes<span class="brand-dot">.</span></a>
  <div class="workspace-label">ML <span>Personal workspace<small>A little more curious, every day.</small></span></div>
  <p class="nav-label">WORKSPACE</p>
  <nav class="workspace-links" aria-label="Sections">{"".join(links)}</nav>
  <div class="nav-bottom"><p>Make room for a question.<br>Leave with a new idea.</p><small>YOUR PERSONAL ML WORKSPACE</small></div>
</aside>
<div class="workspace-content">
<header class="workspace-topbar"><span>Workspace <span class="breadcrumb-divider">›</span> <b>{esc(title)}</b></span>
<button class="theme-toggle" type="button" aria-label="Switch light or dark theme"><span aria-hidden="true">◐</span></button></header>
<main class="wrap">
{body}
</main>
<footer class="wrap footer">
  <p>Generated by <code>llm-updates</code>. Excerpts come from the linked sources; open the source before relying on a claim.</p>
</footer>
</div>
<div class="toast" role="status" aria-live="polite"></div>
<script src="static/app.js" defer></script>
{extra}
</body>
</html>
"""


def learn_page(name: str, interactive: bool) -> str:
    title, slug = LEARN[name]
    content = files("llm_updates").joinpath(f"web/content/{slug}.html").read_text(encoding="utf-8")
    return layout(title, name, content, interactive, scripts=SCRIPTS.get(name, ("viz.js",)))


# Updates ---------------------------------------------------------------------------------


def _meter(label, value) -> str:
    try:
        pct = max(0.0, min(1.0, float(value))) * 100
    except (TypeError, ValueError):
        return ""
    return (
        f'<div class="meter"><span>{label}</span>'
        f'<span class="meter-track"><span class="meter-fill" style="width:{pct:.0f}%"></span></span>'
        f"<span class='meter-value'>{pct:.0f}</span></div>"
    )


def _feedback(entry_id, current, interactive) -> str:
    if not interactive:
        return (
            '<p class="feedback-hint">Rate stories from the local app: '
            "<code>llm-updates serve</code></p>"
        )
    buttons = []
    for label, text in LABELS:
        pressed = "true" if label == current else "false"
        buttons.append(
            f'<button type="button" class="chip" data-label="{label}" '
            f'aria-pressed="{pressed}">{text}</button>'
        )
    return (
        f'<div class="feedback" data-entry="{esc(entry_id)}">'
        f'<span class="feedback-label">Was this useful?</span>{"".join(buttons)}</div>'
    )


def entry_card(n, entry, mode, label, interactive) -> str:
    scores = entry.get("scores") or {}
    topics = "".join(f'<span class="tag">{esc(t)}</span>' for t in entry.get("topics", []))
    update = '<span class="badge badge-accent">Update</span>' if entry.get("is_update") else ""
    basis = "" if entry.get("date_basis") == "publisher" else " · first seen"
    interpretation = ""
    if entry.get("why_relevant"):
        interpretation += (
            '<h4 class="label">Why it matters <em>— interpretation</em></h4>'
            f"<p>{esc(entry['why_relevant'])}</p>"
        )
    if entry.get("concept_explanation"):
        interpretation += (
            '<h4 class="label">Concept <em>— interpretation</em></h4>'
            f"<p>{esc(entry['concept_explanation'])}</p>"
        )
    evidence = "".join(
        f'<li><code>{esc(ev.get("evidence_id"))}</code> <span class="muted">'
        f"{esc(ev.get('locator'))}</span></li>"
        for ev in entry.get("evidence", [])
    )
    uncertainties = "".join(f"<li>{esc(u)}</li>" for u in entry.get("uncertainties", []))
    excerpt_label = "Source excerpt" if mode == "baseline" else "What changed (source reports)"
    return f"""<article class="story card" id="{esc(entry.get("entry_id"))}">
  <div class="story-rank" aria-hidden="true">{n}</div>
  <div class="story-body">
    <div class="story-meta">{nice_date(entry.get("date"))}{basis} · {esc(entry.get("content_scope"))} {update}</div>
    <h3><a href="{href(entry.get("url"))}" target="_blank" rel="noopener noreferrer">{esc(entry.get("title"))}</a></h3>
    <h4 class="label">{excerpt_label}</h4>
    <blockquote class="excerpt">{esc(entry.get("what_changed"))}</blockquote>
    {interpretation}
    <div class="tags">{topics}</div>
    <details class="details">
      <summary>Scores, evidence and caveats</summary>
      <div class="meters">
        {_meter("Overall", scores.get("final_score"))}
        {_meter("Relevance", scores.get("relevance"))}
        {_meter("Recency", scores.get("recency"))}
        {_meter("Novelty", scores.get("novelty"))}
      </div>
      <h4 class="label">Evidence</h4><ul class="plain">{evidence}</ul>
      <h4 class="label">Uncertainties</h4><ul>{uncertainties}</ul>
    </details>
    {_feedback(entry.get("entry_id"), label, interactive)}
  </div>
</article>"""


def _coverage(coverage) -> str:
    rows = []
    sources = coverage.get("sources", [])
    most = max([s.get("fetched_count") or 0 for s in sources] + [1])
    for s in sources:
        status = s.get("status", "unknown")
        count = s.get("fetched_count") or 0
        problem = (
            f' <span class="muted">· {esc(s["error_code"])}</span>' if s.get("error_code") else ""
        )
        rows.append(
            f'<li><span class="dot dot-{esc(status)}" title="{esc(status)}"></span>'
            f'<span class="source-name">{esc(s.get("name"))}{problem}</span>'
            f'<span class="bar"><span style="width:{count / most * 100:.0f}%"></span></span>'
            f'<span class="count">{count}</span></li>'
        )
    return "".join(rows)


def _history(summaries, current) -> str:
    rows = []
    for d in summaries[:12]:
        mark = ' aria-current="page"' if d["digest_id"] == current else ""
        rows.append(
            f'<li><a href="{digest_page(d["digest_id"])}"{mark}>'
            f"<span>{window_label(d['window'])}</span>"
            f'<span class="muted">{d["count"]} stories · {esc(d["status"])}</span></a></li>'
        )
    return "".join(rows)


def _topics(summaries) -> str:
    counts = Counter(t for d in summaries for t in d["topics"]).most_common(8)
    if not counts:
        return '<p class="muted">No topics yet.</p>'
    most = counts[0][1]
    rows = "".join(
        f'<li><span class="source-name">{esc(t)}</span>'
        f'<span class="bar bar-accent"><span style="width:{c / most * 100:.0f}%"></span></span>'
        f'<span class="count">{c}</span></li>'
        for t, c in counts
    )
    return f'<ul class="bars">{rows}</ul>'


EMPTY = """<section class="hero">
  <p class="eyebrow">THE FIELDNOTES BRIEFING</p>
  <h1>No digests yet</h1>
  <p class="lede">Run a collection and your digest will appear here. To try it with fictional demo data:</p>
</section>
<div class="card empty">
<pre><code>llm-updates init --config demo.toml --demo
llm-updates --config demo.toml run --fixture fixtures/demo.jsonl --since 2026-10-07 --until 2026-10-14
llm-updates --config demo.toml serve</code></pre>
<p>For live sources: <code>llm-updates init</code>, then <code>llm-updates run</code>.</p>
<p>While you wait, start with the guides: <a href="classical-ml.html">Classical ML</a>,
<a href="deep-learning.html">Deep learning</a> and <a href="llms.html">LLMs</a>.</p>
</div>"""


def updates_page(summaries, payload, labels, interactive) -> str:
    if payload is None:
        return layout("Updates", "index.html", EMPTY, interactive)
    latest = bool(summaries) and summaries[0]["digest_id"] == payload["digest_id"]
    coverage = payload.get("coverage", {})
    cost = payload.get("cost", {})
    entries = payload.get("entries", [])
    mode = payload.get("mode", "baseline")
    cards = "".join(
        entry_card(n, e, mode, labels.get(e.get("entry_id")), interactive)
        for n, e in enumerate(entries, 1)
    )
    if not cards:
        cards = (
            '<div class="card empty"><p>No eligible new stories cleared the relevance and '
            "novelty filters for this window.</p></div>"
        )
    notices = "".join(f'<div class="notice">{esc(w)}</div>' for w in payload.get("limitations", []))
    status = payload.get("status", "unknown")
    heading = "What's new in LLMs" if latest else "Earlier digest"
    title = "Updates" if latest else f"Digest {window_label(payload.get('window', {}))}"
    body = f"""<section class="hero">
  <p class="eyebrow">{window_label(payload.get("window", {}))}</p>
  <h1>{heading}</h1>
  <p class="news-kicker">What’s changing. <em>Why it matters.</em></p>
  <p class="lede">Up to five stories, ranked by relevance, recency and novelty across your sources.
  Every excerpt is quoted from the linked source.</p>
  <div class="badges">
    <span class="badge status-{esc(status)}">{esc(status)}</span>
    <span class="badge">{esc(mode)} mode</span>
  </div>
</section>
{notices}
<div class="stats">
  <div class="stat"><span class="stat-value">{len(entries)}</span><span class="stat-label">stories</span></div>
  <div class="stat"><span class="stat-value">{coverage.get("succeeded", 0)}/{coverage.get("checked", 0)}</span><span class="stat-label">sources complete</span></div>
  <div class="stat"><span class="stat-value">{cost.get("calls", 0)}</span><span class="stat-label">model calls</span></div>
  <div class="stat"><span class="stat-value">${float(cost.get("accounted_usd") or 0):.2f}</span><span class="stat-label">model cost</span></div>
</div>
<div class="updates-grid">
  <section class="stories" aria-label="Stories">{cards}</section>
  <aside class="sidebar">
    <section class="card panel">
      <h2>Sources</h2>
      <ul class="bars">{_coverage(coverage)}</ul>
      <p class="muted small">Bars show records stored this run.</p>
    </section>
    <section class="card panel">
      <h2>Topics so far</h2>
      {_topics(summaries)}
    </section>
    <section class="card panel">
      <h2>Digests</h2>
      <ul class="history">{_history(summaries, payload["digest_id"])}</ul>
    </section>
  </aside>
</div>"""
    return layout(title, "index.html" if latest else "", body, interactive)


def render(repo, name: str, interactive: bool) -> str | None:
    """Render one page by file name, or None when the name is unknown."""
    if name == "datasets.html":
        if interactive:
            body = '<section class="hero"><p class="eyebrow">YOUR PERSONAL DATA LAB</p><h1>Explore. Predict. Understand.</h1><p class="lede">Turn your datasets into dashboards and testable predictions.</p></section><div id="dataset-root"></div><noscript>Enable JavaScript to use the dataset tools.</noscript>'
        else:
            body = '<section class="hero"><p class="eyebrow">YOUR PERSONAL DATA LAB</p><h1>Datasets stay on your machine.</h1><p class="lede">Uploads, dashboards and prediction tools are available in the local app.</p></section><div class="card panel"><p>Run <code>llm-updates serve</code>, then choose Datasets &amp; tools. Private datasets are never included in a static export.</p></div>'
        return layout(
            "Datasets & tools",
            name,
            body,
            interactive,
            scripts=("datasets.js",) if interactive else (),
        )
    if name in LEARN:
        return learn_page(name, interactive)
    summaries = digest_summaries(repo)
    labels = latest_labels(repo)
    if name == "index.html":
        payload = repo.digest(summaries[0]["digest_id"]) if summaries else None
        return updates_page(summaries, payload, labels, interactive)
    digest_id = name.removesuffix(".html")
    if name.endswith(".html") and DIGEST_ID.match(digest_id) and repo is not None:
        payload = repo.digest(digest_id)
        if payload and payload.get("digest_id") == digest_id:
            return updates_page(summaries, payload, labels, interactive)
    return None

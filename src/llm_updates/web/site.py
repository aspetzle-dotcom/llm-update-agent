"""Static, read-only export of the web app for any static host."""

from __future__ import annotations

from pathlib import Path

from ..rendering import atomic_write
from .pages import LEARN, digest_page, digest_summaries, render
from .server import reader, static_assets


def build_site(database: str, out) -> dict:
    """Write every page plus assets. Feedback notes and model transcripts are never exported."""
    out = Path(out)
    written = []
    with reader(database) as repo:
        names = ["index.html", "datasets.html", *LEARN]
        names += [digest_page(d["digest_id"]) for d in digest_summaries(repo)]
        for name in names:
            atomic_write(out / name, render(repo, name, interactive=False))
            written.append(name)
    for name, body in static_assets().items():
        atomic_write(out / "static" / name, body.decode("utf-8"))
        written.append("static/" + name)
    # GitHub Pages otherwise runs Jekyll, which is unnecessary for these files.
    atomic_write(out / ".nojekyll", "")
    return {"status": "exported", "output": str(out.resolve()), "files": len(written)}

from __future__ import annotations

import html
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from .util import clean, uid


def baseline_entry(row, digest_id):
    passage = row["evidence"][0]
    return {
        "entry_id": uid("entry", digest_id, row["story_id"]),
        "story_id": row["story_id"],
        "item_id": row["item_id"],
        "version_id": row["version_id"],
        "title": row["metadata"]["title"],
        "url": row["metadata"]["canonical_url"],
        "date": row["scores"]["date"],
        "date_basis": row["scores"]["date_basis"],
        "content_scope": row["metadata"]["content_scope"],
        "topics": row["scores"]["topics"],
        "scores": row["scores"],
        "is_update": row["scores"]["is_update"],
        "what_changed": passage["passage_text"],
        "why_relevant": "",
        "concept_explanation": "",
        "uncertainties": ["Source excerpt only; no independent verification or model explanation."],
        "claims": [
            {
                "schema_version": "1",
                "kind": "source_fact",
                "text": passage["passage_text"],
                "evidence_ids": [passage["evidence_id"]],
                "numeric_context": [],
            }
        ],
        "evidence": [passage],
        "experiment": None,
    }


def safe(value):
    value = html.escape(clean(str(value)), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def markdown(payload):
    lines = [
        "# Personal LLM updates",
        "",
        f"**{safe(payload['mode'])} · {safe(payload['status'])}**",
        "",
        f"Window: {safe(payload['window']['since'])} to {safe(payload['window']['until'])} (end exclusive).",
        "",
        f"Run: `{payload['run_id']}` · Digest: `{payload['digest_id']}`",
        "",
    ]
    if not payload["entries"]:
        lines.extend(["No eligible new stories cleared the relevance and novelty filters.", ""])
    for n, e in enumerate(payload["entries"], 1):
        url = quote(e["url"], safe="/:?=&%")
        lines += [
            f"## {n}. {'Update: ' if e['is_update'] else ''}[{safe(e['title'])}]({url})",
            "",
            f"{safe(e['date'])} · {safe(e['date_basis'])} · {safe(e['content_scope'])}",
            "",
            "**Source excerpt**"
            if payload["mode"] == "baseline"
            else "**What changed (source reports)**",
            "",
            safe(e["what_changed"]),
            "",
        ]
        if e["why_relevant"]:
            lines += [
                "**Why it matters — interpretation**",
                "",
                safe(e["why_relevant"]),
                "",
                "**Concept — explanatory interpretation**",
                "",
                safe(e["concept_explanation"]),
                "",
            ]
        lines += ["**Uncertainties**", ""] + [f"- {safe(u)}" for u in e["uncertainties"]] + [""]
        lines += ["**Evidence**", ""]
        for ev in e["evidence"]:
            lines += [
                f"- [{safe(ev['evidence_id'])}]({url}) · `{ev['version_id']}` · {safe(ev['locator'])}"
            ]
        lines += ["", f"Feedback: `llm-updates feedback {e['entry_id']} useful`", ""]
    coverage = payload["coverage"]
    lines += [
        "## Coverage and cost",
        "",
        f"Checked {coverage['checked']} sources; {coverage['succeeded']} completed the declared window.",
        "",
    ]
    for source in coverage["sources"]:
        desc = f"{source['name']}: {source['status']}; {source['fetched_count']} stored records"
        if source["error_code"]:
            desc += f"; error {source['error_code']}"
        desc += f"; last full success {source['last_success_at'] or 'never'}"
        if source["warnings"]:
            desc += "; " + "; ".join(source["warnings"])
        lines.append("- " + safe(desc))
    lines += [
        "",
        f"Model calls: {payload['cost']['calls']}; accounted cost USD {payload['cost']['accounted_usd']:.6f}.",
        f"Uncertain reservations: {payload['cost']['uncertain_calls']}.",
        "",
    ]
    lines += ["- " + safe(w) for w in payload["limitations"]]
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def export(payload, folder):
    folder = Path(folder)
    # Both exports are reconstructed from the committed digest, never new model calls.
    atomic_write(
        folder / (payload["digest_id"] + ".json"),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write(folder / (payload["digest_id"] + ".md"), markdown(payload))
    return str(folder / (payload["digest_id"] + ".md"))

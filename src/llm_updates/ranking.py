from __future__ import annotations

import re
from datetime import datetime

from .util import dumps


def scores(row, profile, since, until, config, mode="baseline"):
    metadata = row["metadata"]
    text = (metadata["title"] + " " + row["normalized_text"]).lower()
    topics = [
        name
        for name, topic in profile.topics.items()
        if any(
            re.search(r"(?<!\w)" + re.escape(k.lower()) + r"(?!\w)", text) for k in topic.keywords
        )
    ]
    # Best topic match plus modest credit for breadth; avoids requiring every topic at once.
    matched = sorted((profile.topics[name].weight for name in topics), reverse=True)
    relevance = min(1, (matched[0] + sum(matched[1:]) * 0.15) / 1.5) if matched else 0
    published = metadata.get("published_at") or row["first_seen_at"]
    stamp, end = datetime.fromisoformat(published), datetime.fromisoformat(until)
    age = max(0, (end - stamp).total_seconds() / 86400)
    recency = 2 ** (-age / config.recency_half_life_days)
    delivered = row["delivered_versions"]
    update = bool(metadata.get("substantive_update"))
    correction = bool(metadata.get("correction") or metadata.get("withdrawn"))
    novelty = 0.6 if delivered else 1.0
    reason = None
    if row["version_id"] in delivered:
        novelty, reason = 0, "previously_delivered_version"
    elif delivered and not update and not correction:
        reason = "no_substantive_update"
    elif not (datetime.fromisoformat(since) <= stamp < end):
        # Old-story updates need a publisher update inside the window, not a new fetch timestamp.
        updated = metadata.get("updated_at")
        if not (
            delivered
            and (update or correction)
            and updated
            and datetime.fromisoformat(since) <= datetime.fromisoformat(updated) < end
        ):
            reason = "outside_window"
    if any(t.lower() in text for t in profile.excluded_topics) and not correction:
        reason = reason or "excluded_topic"
    if relevance < config.minimum_relevance and not (correction and delivered):
        reason = reason or "below_relevance_threshold"
    if correction and delivered and reason is None:
        # Retractions/corrections are not hidden by novelty or relevance filtering.
        relevance, novelty = max(0.9, relevance), 1
    final = 0.55 * relevance + 0.25 * recency + 0.20 * novelty
    if mode == "newest":
        final = recency
    elif mode == "keyword-recency":
        final = 0.7 * relevance + 0.3 * recency
    return {
        "relevance": relevance,
        "recency": recency,
        "novelty": novelty,
        "final_score": final,
        "exclusion_reason": reason,
        "topics": topics,
        "is_update": bool(delivered and (update or correction)),
        "correction": correction,
        "date": published,
        "date_basis": metadata["date_basis"],
    }


def rank(repo, rid, config, since, until, mode="baseline", limit=12):
    rows = repo.candidates(rid, [s.source_id for s in config.sources if s.enabled])
    with repo.transaction():
        for row in rows:
            row["scores"] = scores(row, config.profile, since, until, config, mode)
            s = row["scores"]
            repo.db.execute(
                """INSERT OR REPLACE INTO run_items VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rid,
                    row["item_id"],
                    row["version_id"],
                    row["story_id"],
                    "excluded" if s["exclusion_reason"] else "eligible",
                    s["relevance"],
                    s["recency"],
                    s["novelty"],
                    s["final_score"],
                    s["exclusion_reason"],
                    dumps(s),
                ),
            )
    eligible = sorted(
        (r for r in rows if not r["scores"]["exclusion_reason"]),
        key=lambda r: (
            -r["scores"]["final_score"],
            -datetime.fromisoformat(r["scores"]["date"]).timestamp(),
            r["canonical_key"],
        ),
    )
    selected, stories, families = [], set(), {}
    for row in eligible:
        if row["story_id"] in stories or families.get(row["kind"], 0) >= config.source_family_cap:
            continue
        stories.add(row["story_id"])
        families[row["kind"]] = families.get(row["kind"], 0) + 1
        selected.append(row)
        if len(selected) == limit:
            break
    return selected

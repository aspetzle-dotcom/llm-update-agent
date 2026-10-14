"""Small deterministic evaluation harness. Labels are supplied by a human or synthetic tests."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .ranking import scores
from .util import date


def evaluate(path, config, mode="baseline"):
    rows = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    story_splits = {}
    groups = defaultdict(list)
    for r in rows:
        if r["relevance"] not in (0, 1, 2) or r["split"] not in ("train", "test"):
            raise ValueError("invalid evaluation label")
        split = story_splits.setdefault(r["story_id"], r["split"])
        if split != r["split"]:
            raise ValueError("story leakage across train/test splits")
        groups[(r["split"], r["day"])].append(r)
    train_days = [d for s, d in groups if s == "train"]
    test_days = [d for s, d in groups if s == "test"]
    if train_days and test_days and max(train_days) >= min(test_days):
        raise ValueError("test days must follow training days")
    metrics = []
    for (split, day), records in sorted(groups.items()):
        ranked = []
        for record in records:
            published = date(record["published_at"])
            row = {
                "metadata": {
                    "title": record["title"],
                    "published_at": published,
                    "date_basis": "publisher",
                },
                "normalized_text": record["text"],
                "first_seen_at": published,
                "version_id": record["item_id"],
                "delivered_versions": [],
            }
            score = scores(row, config.profile, date(record["since"]), date(day), config, mode)
            if not score["exclusion_reason"]:
                ranked.append((score["final_score"], published, record))
        ranked.sort(
            key=lambda x: (
                -x[0],
                -__import__("datetime").datetime.fromisoformat(x[1]).timestamp(),
                x[2]["item_id"],
            )
        )
        selected, seen, families = [], set(), {}
        for _, _, record in ranked:
            if (
                record["story_id"] in seen
                or families.get(record["kind"], 0) >= config.source_family_cap
            ):
                continue
            seen.add(record["story_id"])
            families[record["kind"]] = families.get(record["kind"], 0) + 1
            selected.append(record)
            if len(selected) == 5:
                break
        hits = sum(r["relevance"] == 2 for r in selected)
        relevant = {r["story_id"] for _, _, r in ranked if r["relevance"] == 2}
        metrics.append(
            {
                "split": split,
                "day": day,
                "sample_size": len(records),
                "selected_ids": [r["item_id"] for r in selected],
                "precision_at_5": hits / 5,
                "returned_precision": hits / len(selected) if selected else None,
                "eligible_relevant_stories": len(relevant),
                "selection_coverage": hits / len(relevant) if relevant else None,
                "duplicate_rate": 0.0,
            }
        )
    return {
        "schema_version": "1",
        "mode": mode,
        "items": len(rows),
        "days": metrics,
        "model_cost_usd": 0,
        "limitation": "Ranking metrics only. Human grounding and live ingestion coverage are not measured.",
    }

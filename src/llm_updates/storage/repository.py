from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..config import Config
from ..models import CollectorResult, NormalizedItem
from ..util import dumps, now, sha, uid


class Repository:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            raise ValueError("database schema is newer than this application")
        if version == 0:
            sql = Path(__file__).with_name("001_initial.sql").read_text(encoding="utf-8")
            self.db.executescript("BEGIN IMMEDIATE;\n" + sql + "\nPRAGMA user_version=1;\nCOMMIT;")
        if version < 2:
            sql = (
                Path(__file__).with_name("002_immutable_snapshots.sql").read_text(encoding="utf-8")
            )
            self.db.executescript("BEGIN IMMEDIATE;\n" + sql + "\nPRAGMA user_version=2;\nCOMMIT;")

    def close(self):
        self.db.close()

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    @contextmanager
    def transaction(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            yield

    def event(self, run_id, kind, details, entity=None):
        self.db.execute(
            "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
            ("event_" + uuid.uuid4().hex, run_id, now(), kind, entity, dumps(details)),
        )

    def configure(self, config: Config):
        with self.db:
            for source in config.sources:
                self.db.execute(
                    """INSERT INTO sources(source_id,kind,name,endpoint,policy_json,enabled)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                  name=excluded.name, endpoint=excluded.endpoint, policy_json=excluded.policy_json,
                  enabled=excluded.enabled""",
                    (
                        source.source_id,
                        source.kind,
                        source.name,
                        source.endpoint,
                        source.model_dump_json(),
                        int(source.enabled),
                    ),
                )

    def start(self, config: Config, invocation: dict, parent: str | None = None) -> dict:
        key = sha({"config": config.model_dump(), "invocation": invocation, "parent": parent})
        run = self.one("SELECT * FROM runs WHERE idempotency_key=?", (key,))
        if run:
            return run
        rid = uid("run", key)
        with self.transaction():
            self.db.execute(
                """INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rid,
                    key,
                    parent,
                    now(),
                    None,
                    "queued",
                    "queued",
                    sha(config.model_dump()),
                    sha(config.profile.model_dump()),
                    "",
                    "1",
                    config.limits.model_dump_json(),
                    "{}",
                    dumps({**invocation, "config_snapshot": config.model_dump()}),
                    1,
                ),
            )
            for s in config.sources:
                if s.enabled:
                    self.db.execute(
                        """INSERT INTO source_runs
                      (run_id,source_id,status,last_attempt_at) VALUES(?,?,?,?)""",
                        (rid, s.source_id, "pending", now()),
                    )
            self.event(rid, "created", {"source_set_version": config.source_set_version})
        return self.one("SELECT * FROM runs WHERE run_id=?", (rid,))

    def state(self, rid, status, stage=None, outcome=None):
        with self.db:
            self.db.execute(
                """UPDATE runs SET status=?, stage=COALESCE(?,stage),
                outcome_json=COALESCE(?,outcome_json), ended_at=? WHERE run_id=?""",
                (
                    status,
                    stage,
                    dumps(outcome) if outcome is not None else None,
                    now() if status in ("succeeded", "partial", "failed", "interrupted") else None,
                    rid,
                ),
            )
            self.event(rid, "state", {"status": status, "stage": stage})

    def ingest(self, item: NormalizedItem, rid: str) -> tuple[str, str]:
        """Caller owns the page transaction; item, evidence, and cursor commit together."""
        iid = uid("item", item.canonical_key)
        alias = self.one(
            "SELECT item_id FROM item_aliases WHERE source_id=? AND external_id=?",
            (item.source_id, item.external_id),
        )
        if alias and alias["item_id"] != iid:
            raise ValueError("alias identity conflict")
        self.db.execute(
            """INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(canonical_key) DO NOTHING""",
            (
                iid,
                item.canonical_key,
                item.kind,
                item.canonical_url,
                item.title,
                item.fetched_at,
                item.published_at,
                "publisher" if item.published_at else "first_seen",
                "withdrawn" if item.withdrawn else "active",
            ),
        )
        self.db.execute(
            "INSERT OR IGNORE INTO item_aliases VALUES(?,?,?,?,?)",
            (
                uid("alias", item.source_id, item.external_id),
                iid,
                item.source_id,
                item.external_id,
                item.canonical_url,
            ),
        )
        content_hash = sha(
            {
                "text": item.content_text,
                "title": item.title,
                "authors": item.authors,
                "scope": item.content_scope,
                "published_at": item.published_at,
                "updated_at": item.updated_at,
                "canonical_url": item.canonical_url,
                "withdrawn": item.withdrawn,
                "correction": item.correction,
            }
        )
        vid = uid("version", iid, content_hash)
        metadata = item.model_dump(exclude={"content_text", "raw_hash", "fetched_at"})
        metadata["date_basis"] = "publisher" if item.published_at else "first_seen"
        exists = self.one("SELECT version_id FROM versions WHERE version_id=?", (vid,))
        if not exists:
            previous = self.one(
                "SELECT * FROM versions WHERE item_id=? ORDER BY rowid DESC LIMIT 1", (iid,)
            )
            if previous and previous["extractor_version"] != "1":
                raise ValueError("extractor migration required")
            if previous:
                import difflib

                metadata["diff"] = "\n".join(
                    difflib.unified_diff(
                        previous["normalized_text"].splitlines(),
                        item.content_text.splitlines(),
                        fromfile=previous["version_id"],
                        tofile=vid,
                        lineterm="",
                    )
                )
                ratio = difflib.SequenceMatcher(
                    None, previous["normalized_text"], item.content_text
                ).ratio()
                metadata["substantive_update"] = (
                    ratio < 0.95
                    and abs(len(previous["normalized_text"]) - len(item.content_text)) >= 20
                )
                # Same-length rewrites can also be substantial.
                metadata["substantive_update"] |= ratio < 0.8
            self.db.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    vid,
                    iid,
                    item.publisher_version,
                    content_hash,
                    item.raw_hash,
                    item.content_text,
                    "1",
                    item.fetched_at,
                    dumps(metadata),
                    None,
                    rid,
                ),
            )
            start = 0
            while start < len(item.content_text):
                end = min(start + 900, len(item.content_text))
                if end < len(item.content_text):
                    boundary = item.content_text.rfind(" ", start + 450, end)
                    if boundary > start:
                        end = boundary
                passage = item.content_text[start:end]
                self.db.execute(
                    "INSERT INTO evidence VALUES(?,?,?,?,?,?,?)",
                    (
                        uid("ev", vid, start, end),
                        vid,
                        start,
                        end,
                        passage,
                        f"chars:{start}-{end};{item.content_scope}",
                        sha(passage),
                    ),
                )
                start = end
            self.db.execute(
                "UPDATE items SET title=?,status=?,published_at=?,date_basis=? WHERE item_id=?",
                (
                    item.title,
                    "withdrawn" if item.withdrawn else "active",
                    item.published_at,
                    "publisher" if item.published_at else "first_seen",
                    iid,
                ),
            )
        story = self.one("SELECT story_id FROM story_members WHERE item_id=?", (iid,))
        if not story:
            sid = uid("story", iid)
            self.db.execute("INSERT INTO stories VALUES(?,?,?)", (sid, item.title, item.fetched_at))
            self.db.execute("INSERT INTO story_members VALUES(?,?,?,?)", (sid, iid, "identity", 1))
        for key in item.related_keys:
            other = self.one("SELECT item_id FROM items WHERE canonical_key=?", (key,))
            if other:
                self.group(rid, [iid, other["item_id"]], "explicit_relationship")
        return iid, vid

    def group(self, rid, item_ids, method="manual"):
        rows = [
            self.one("SELECT story_id FROM story_members WHERE item_id=?", (i,)) for i in item_ids
        ]
        if any(row is None for row in rows):
            raise ValueError("unknown grouping item")
        story_ids = sorted({row["story_id"] for row in rows})
        keep = story_ids[0]
        for old in story_ids[1:]:
            self.db.execute(
                "UPDATE story_members SET story_id=?,method=? WHERE story_id=?", (keep, method, old)
            )
            self.event(rid, "story_grouped", {"from": old, "to": keep, "method": method})
        return keep

    def commit_page(self, rid, source_id, result: CollectorResult, terminal=False):
        with self.transaction():
            for item in result.items:
                if item.source_id != source_id:
                    raise ValueError("collector emitted another source's item")
                self.ingest(item, rid)
            self.db.execute(
                """UPDATE source_runs SET cursor_after=?, fetched_count=fetched_count+?,
              status=?, warnings_json=?, last_attempt_at=?,error_code=NULL WHERE run_id=? AND source_id=?""",
                (
                    result.cursor,
                    len(result.items),
                    ("succeeded" if result.complete else "incomplete") if terminal else "fetching",
                    dumps(result.warnings),
                    now(),
                    rid,
                    source_id,
                ),
            )
            self.db.execute(
                "UPDATE sources SET cursor=?,next_allowed_at=COALESCE(?,next_allowed_at) WHERE source_id=?",
                (result.cursor, result.retry_after, source_id),
            )
            if terminal and result.complete:
                self.db.execute(
                    "UPDATE sources SET last_success_at=? WHERE source_id=?", (now(), source_id)
                )
            self.event(
                rid, "checkpoint", {"cursor": result.cursor, "count": len(result.items)}, source_id
            )

    def resolve_relationships(self, rid):
        with self.transaction():
            for row in self.all("SELECT item_id,metadata_json FROM versions ORDER BY rowid"):
                for key in json.loads(row["metadata_json"]).get("related_keys", []):
                    other = self.one("SELECT item_id FROM items WHERE canonical_key=?", (key,))
                    if other:
                        self.group(rid, [row["item_id"], other["item_id"]], "explicit_relationship")

    def candidates(self, rid, enabled_sources):
        marks = ",".join("?" for _ in enabled_sources) or "NULL"
        rows = self.all(
            f"""SELECT i.*,v.version_id,v.content_hash,v.normalized_text,v.metadata_json,
          v.fetched_at,s.story_id FROM items i JOIN versions v ON v.item_id=i.item_id
          JOIN story_members s ON s.item_id=i.item_id
          WHERE v.rowid=(SELECT MAX(v2.rowid) FROM versions v2 WHERE v2.item_id=i.item_id)
          AND EXISTS(SELECT 1 FROM item_aliases a WHERE a.item_id=i.item_id AND a.source_id IN ({marks}))
          ORDER BY i.canonical_key""",
            enabled_sources,
        )
        for row in rows:
            row["metadata"] = json.loads(row["metadata_json"])
            row["evidence"] = self.all(
                "SELECT * FROM evidence WHERE version_id=? ORDER BY start_char",
                (row["version_id"],),
            )
            prior = self.all(
                """SELECT DISTINCT e.version_id FROM claim_evidence ce
              JOIN evidence e ON e.evidence_id=ce.evidence_id JOIN claims c ON c.claim_id=ce.claim_id
              JOIN digest_entries de ON de.entry_id=c.entry_id JOIN digests d ON d.digest_id=de.digest_id
              JOIN versions v ON v.version_id=e.version_id JOIN story_members sm ON sm.item_id=v.item_id
              WHERE sm.story_id=? AND d.run_id!=?""",
                (row["story_id"], rid),
            )
            row["delivered_versions"] = [p["version_id"] for p in prior]
        return rows

    def coverage(self, rid):
        rows = self.all(
            """SELECT sr.*,s.name,s.last_success_at FROM source_runs sr
            JOIN sources s USING(source_id) WHERE run_id=? ORDER BY source_id""",
            (rid,),
        )
        for row in rows:
            row["warnings"] = json.loads(row.pop("warnings_json"))
        return {
            "checked": len(rows),
            "succeeded": sum(r["status"] == "succeeded" for r in rows),
            "failed": [r["source_id"] for r in rows if r["status"] == "failed"],
            "incomplete": [r["source_id"] for r in rows if r["status"] == "incomplete"],
            "sources": rows,
        }

    def digest(self, identifier):
        row = self.one(
            "SELECT * FROM digests WHERE digest_id=? OR run_id=?", (identifier, identifier)
        )
        return json.loads(row["payload_json"]) if row else None

    def save_digest(self, payload):
        with self.transaction():
            self.db.execute(
                "INSERT INTO digests VALUES(?,?,?,?,?)",
                (payload["digest_id"], payload["run_id"], payload["status"], dumps(payload), now()),
            )
            for position, entry in enumerate(payload["entries"], 1):
                self.db.execute(
                    "INSERT INTO digest_entries VALUES(?,?,?,?,?)",
                    (
                        entry["entry_id"],
                        payload["digest_id"],
                        entry["story_id"],
                        position,
                        dumps(entry),
                    ),
                )
                for n, claim in enumerate(entry["claims"]):
                    cid = uid("claim", entry["entry_id"], n)
                    self.db.execute(
                        "INSERT INTO claims VALUES(?,?,?,?,?)",
                        (
                            cid,
                            entry["entry_id"],
                            claim["text"],
                            claim["kind"],
                            "exact_excerpt" if claim["kind"] == "source_fact" else "interpretation",
                        ),
                    )
                    for eid in claim["evidence_ids"]:
                        self.db.execute(
                            "INSERT INTO claim_evidence VALUES(?,?,?)", (cid, eid, "supports")
                        )

    def feedback(self, entry_id, label, note):
        from ..util import clean

        entry = self.one("SELECT * FROM digest_entries WHERE entry_id=?", (entry_id,))
        if not entry:
            raise ValueError("entry does not exist")
        with self.transaction():
            self.db.execute(
                "INSERT INTO feedback VALUES(?,?,?,?,?)",
                (
                    "feedback_" + uuid.uuid4().hex,
                    entry_id,
                    label,
                    clean(note)[:1000] if note else None,
                    now(),
                ),
            )
        return {"entry_id": entry_id, "label": label}

    def effective_profile(self, config):
        """Recompute from the explicit profile and latest label per entry, never from silence."""
        profile = config.profile.model_copy(deep=True)
        latest = self.all("""SELECT f.*,de.text_json FROM feedback f JOIN digest_entries de USING(entry_id)
            WHERE f.rowid=(SELECT MAX(f2.rowid) FROM feedback f2 WHERE f2.entry_id=f.entry_id)""")
        adjustments = {}
        for f in latest:
            entry = json.loads(f["text_json"])
            for topic in entry.get("topics", []):
                adjustments.setdefault(topic, {"useful": 0, "skip": 0})
                if f["label"] in ("useful", "skip"):
                    adjustments[topic][f["label"]] += 1
        for name, a in adjustments.items():
            if name in profile.topics:
                delta = min(0.3, 0.05 * a["useful"]) - (
                    min(0.2, 0.02 * a["skip"]) if a["skip"] >= 3 else 0
                )
                profile.topics[name].weight = min(3, max(0, profile.topics[name].weight + delta))
        settings = profile.model_dump_json()
        old = self.one("SELECT * FROM profiles ORDER BY version DESC LIMIT 1")
        if old is None or old["settings_json"] != settings:
            with self.db:
                version = old["version"] + 1 if old else 1
                self.db.execute(
                    "INSERT INTO profiles VALUES(?,?,?,?)",
                    (uid("profile", version, settings), version, settings, now()),
                )
        return profile

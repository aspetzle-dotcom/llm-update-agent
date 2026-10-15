import json
import sqlite3
import time
from pathlib import Path

import pytest

from llm_updates.coordinator import Coordinator
from llm_updates.models import CollectorResult, NormalizedItem
from llm_updates.storage import Repository


def collect(repo, config, since, until, key):
    run = repo.start(config, {"since": since, "until": until, "key": key})
    Coordinator(repo, config, fetcher_factory=lambda *args: None).collect(
        run["run_id"], since, until, None, time.monotonic() + 30
    )
    return run["run_id"]


@pytest.fixture
def windows(repo, config, monkeypatch):
    config.sources = config.sources[:1]
    calls = []

    def pages(source, fetcher, since, until, cap, cursor, already):
        calls.append((since, until, cursor))
        yield CollectorResult(items=[], complete=True), True

    monkeypatch.setattr("llm_updates.coordinator.pages", pages)
    return calls


def test_historical_success_uses_window_end(repo, config, windows):
    collect(repo, config, "2026-10-01T00:00:00+00:00", "2026-10-10T00:00:00+00:00", "history")
    source = repo.one(
        "SELECT last_success_at FROM sources WHERE source_id=?", (config.sources[0].source_id,)
    )
    assert source["last_success_at"] == "2026-10-10T00:00:00+00:00"
    collect(repo, config, "2026-10-08T00:00:00+00:00", "2026-10-16T00:00:00+00:00", "next")
    assert windows[-1][0] == "2026-10-08T00:00:00+00:00"


def test_disjoint_history_does_not_skip_missing_coverage(repo, config, windows):
    collect(repo, config, "2026-10-12T00:00:00+00:00", "2026-10-14T00:00:00+00:00", "recent")
    collect(repo, config, "2026-10-01T00:00:00+00:00", "2026-10-16T00:00:00+00:00", "backfill")
    assert windows[-1][0] == "2026-10-01T00:00:00+00:00"
    collect(repo, config, "2026-09-01T00:00:00+00:00", "2026-09-10T00:00:00+00:00", "older")
    assert (
        repo.one(
            "SELECT last_success_at FROM sources WHERE source_id=?", (config.sources[0].source_id,)
        )["last_success_at"]
        == "2026-10-16T00:00:00+00:00"
    )


def test_resume_preserves_query_window_and_cursor(repo, config, windows, monkeypatch):
    since, until = "2026-10-07T00:00:00+00:00", "2026-10-15T00:00:00+00:00"
    collect(repo, config, since, "2026-10-14T00:00:00+00:00", "prior")
    calls = []

    def interrupted(source, fetcher, effective_since, effective_until, cap, cursor, already):
        calls.append((effective_since, effective_until, cursor, already))
        if cursor is None:
            yield CollectorResult(items=[], cursor='{"start":50}', complete=False), False
            raise KeyboardInterrupt
        yield CollectorResult(items=[], complete=True), True

    monkeypatch.setattr("llm_updates.coordinator.pages", interrupted)
    run = repo.start(config, {"key": "interrupted"})
    coordinator = Coordinator(repo, config, fetcher_factory=lambda *args: None)
    with pytest.raises(KeyboardInterrupt):
        coordinator.collect(run["run_id"], since, until, None, time.monotonic() + 30)
    # Another run may advance shared source coverage before this run resumes.
    with repo.db:
        repo.db.execute("UPDATE sources SET last_success_at='2026-10-16T00:00:00+00:00'")
    coordinator.collect(
        run["run_id"], since, "2026-10-16T00:00:00+00:00", None, time.monotonic() + 30
    )
    assert calls[0][0] == calls[1][0] == "2026-10-12T00:00:00+00:00"
    assert calls[0][1] == calls[1][1] == until
    assert calls[1][2] == '{"start":50}'


def test_reverted_content_becomes_current_without_mutating_versions(repo, config, fixture_path):
    item = NormalizedItem.model_validate_json(fixture_path.read_text().splitlines()[0])
    rid = repo.start(config, {"key": "revert"})["run_id"]
    with repo.transaction():
        iid, original = repo.ingest(item, rid)
    changed = item.model_copy(deep=True)
    changed.title = "Changed title"
    changed.withdrawn = True
    changed.content_text += " An important retrieval evaluation change with a new baseline."
    with repo.transaction():
        _, revised = repo.ingest(changed, rid)
    before = repo.all("SELECT * FROM versions ORDER BY rowid")
    with repo.transaction():
        _, observed = repo.ingest(item, rid)
    row = next(r for r in repo.candidates(rid, [item.source_id]) if r["item_id"] == iid)
    assert observed == original != revised
    assert row["version_id"] == original
    assert row["normalized_text"] == item.content_text
    assert row["title"] == item.title and row["status"] == "active"
    assert repo.all("SELECT * FROM versions ORDER BY rowid") == before
    # Subsequent diffs must compare against the most recently observed content.
    changed.content_text = item.content_text + " A distinct follow-up."
    with repo.transaction():
        _, followup = repo.ingest(changed, rid)
    metadata = json.loads(
        repo.one("SELECT metadata_json FROM versions WHERE version_id=?", (followup,))[
            "metadata_json"
        ]
    )
    assert metadata["diff"].startswith("--- " + original)


def test_schema_two_upgrade_preserves_evidence_and_restarts_unsafe_cursors(tmp_path):
    path = tmp_path / "old.sqlite3"
    storage = Path(__file__).parents[1] / "src/llm_updates/storage"
    db = sqlite3.connect(path)
    for name in ("001_initial.sql", "002_immutable_snapshots.sql"):
        db.executescript((storage / name).read_text())
    db.execute("PRAGMA user_version=2")
    db.execute(
        "INSERT INTO sources(source_id,kind,name,endpoint,policy_json,enabled,last_success_at) VALUES('a','arxiv','A','https://example.org','{}',1,'2026-10-15')"
    )
    db.execute(
        "INSERT INTO runs VALUES('r','key',NULL,'2026-10-15',NULL,'interrupted','collecting','','','','1','{}','{}','{}',1)"
    )
    db.execute(
        "INSERT INTO source_runs(run_id,source_id,status,cursor_after,fetched_count,last_attempt_at) VALUES('r','a','fetching','cursor',50,'2026-10-15')"
    )
    db.execute(
        "INSERT INTO items VALUES('i','key','arxiv','https://example.org','Title','2026-10-15',NULL,'first_seen','active')"
    )
    db.execute(
        "INSERT INTO versions VALUES('v','i',NULL,'hash','raw','Text','1','2026-10-15','{}',NULL,'r')"
    )
    db.execute("INSERT INTO evidence VALUES('e','v',0,4,'Text','chars:0-4','hash')")
    db.commit()
    db.close()
    repo = Repository(str(path))
    assert repo.db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert repo.one("SELECT * FROM current_versions") == {"item_id": "i", "version_id": "v"}
    assert repo.one("SELECT passage_text FROM evidence")["passage_text"] == "Text"
    state = repo.one("SELECT * FROM source_runs")
    assert state["cursor_after"] is None and state["fetched_count"] == 0
    assert state["status"] == "pending"
    assert repo.one("SELECT last_success_at FROM sources")["last_success_at"] is None
    repo.close()

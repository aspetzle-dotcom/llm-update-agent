import json
import sqlite3

import pytest
from conftest import SINCE, UNTIL
from pydantic import ValidationError

from llm_updates.coordinator import Coordinator, resume_run
from llm_updates.models import CollectorResult, NormalizedItem
from llm_updates.rendering import markdown
from llm_updates.util import canonical_url, sha


def test_twenty_fixture_observations_are_idempotent(repo, config, fixture_path):
    records = fixture_path.read_text().splitlines()
    assert len(records) == 20
    first, path = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)

    def counts():
        return [
            repo.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
            for t in ("items", "versions", "evidence", "digest_entries")
        ]

    before = counts()
    second, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)
    assert first == second and counts() == before
    assert before[:2] == [18, 18]
    assert len(first["entries"]) == 5
    assert len({e["story_id"] for e in first["entries"]}) == 5
    assert first["coverage"]["succeeded"] == 8
    assert path.endswith(".md")
    for ev in repo.all(
        "SELECT e.*,v.normalized_text FROM evidence e JOIN versions v USING(version_id)"
    ):
        assert ev["normalized_text"][ev["start_char"] : ev["end_char"]] == ev["passage_text"]
        assert sha(ev["passage_text"]) == ev["passage_hash"]


def test_new_run_does_not_repeat_delivered_versions(repo, config, fixture_path):
    first, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)
    second, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path, key="second")
    assert not (
        {e["version_id"] for e in first["entries"]} & {e["version_id"] for e in second["entries"]}
    )


def test_unknown_dates_and_explicit_grouping(repo, config, fixture_path):
    Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)
    unknown = repo.one("SELECT * FROM items WHERE canonical_key='synthetic:15'")
    assert unknown["published_at"] is None and unknown["date_basis"] == "first_seen"
    groups = repo.all("""SELECT sm.story_id FROM story_members sm JOIN items i USING(item_id)
        WHERE canonical_key IN ('synthetic:04','synthetic:17')""")
    # Fixtures are ingested source-by-source; a forward relationship is resolved after collection.
    assert len({g["story_id"] for g in groups}) == 1


def test_invalid_contract_and_foreign_keys(repo, fixture_path):
    record = json.loads(fixture_path.read_text().splitlines()[0])
    with pytest.raises(ValidationError):
        NormalizedItem.model_validate({**record, "execute_shell": True})
    with pytest.raises(ValidationError):
        NormalizedItem.model_validate({**record, "title": "\x1b\x00"})
    assert repo.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError), repo.db:
        repo.db.execute("INSERT INTO story_members VALUES('missing','missing','test',1)")


def test_version_changes_and_transaction_rollback(repo, config, fixture_path):
    first, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)
    item = NormalizedItem.model_validate_json(fixture_path.read_text().splitlines()[0])
    item.content_text += " A substantive retrieval evaluation section adds explicit failure categories and a revised baseline."
    item.updated_at = "2026-10-13T20:00:00+00:00"
    with repo.transaction():
        repo.ingest(item, first["run_id"])
    versions = repo.all(
        "SELECT * FROM versions WHERE item_id=(SELECT item_id FROM items WHERE canonical_key=?)",
        (item.canonical_key,),
    )
    assert len(versions) == 2
    assert any(json.loads(v["metadata_json"]).get("substantive_update") for v in versions)
    bad = item.model_copy(
        update={"source_id": "missing-source", "canonical_key": "bad-item", "external_id": "bad"}
    )
    with pytest.raises(sqlite3.IntegrityError):
        with repo.transaction():
            repo.ingest(bad, first["run_id"])
    assert not repo.one("SELECT * FROM items WHERE canonical_key='bad-item'")


def test_export_failure_reuses_committed_digest(repo, config, fixture_path, monkeypatch):
    import llm_updates.coordinator as module

    original = module.export

    def fail(*args):
        raise OSError("simulated disk error")

    monkeypatch.setattr(module, "export", fail)
    with pytest.raises(OSError):
        Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)
    run = repo.one("SELECT * FROM runs")
    assert run["status"] == "failed" and repo.digest(run["run_id"])
    monkeypatch.setattr(module, "export", original)
    result, path = resume_run(repo, run["run_id"])
    assert result["status"] == "succeeded" and path
    assert repo.one("SELECT COUNT(*) AS n FROM digests")["n"] == 1


def test_model_failure_falls_back_and_escapes_html(repo, config, fixture_path):
    def unavailable():
        raise ValueError("secret-key-must-not-be-logged")

    result, _ = Coordinator(repo, config, provider_factory=unavailable).run(
        SINCE, UNTIL, fixture=fixture_path, mode="research"
    )
    assert result["status"] == "partial" and result["mode"] == "baseline"
    assert "secret-key" not in json.dumps(result)
    result["entries"][0]["title"] = "<script>alert(1)</script>\x1b[31m"
    assert "<script>" not in markdown(result) and "\x1b" not in markdown(result)


def test_canonical_url_preserves_meaningful_query():
    assert (
        canonical_url("https://EXAMPLE.org/post?id=2&utm_source=test#part")
        == "https://example.org/post?id=2"
    )


def test_page_checkpoint_rolls_back_with_bad_item(repo, config, fixture_path):
    run = repo.start(config, {})
    item = NormalizedItem.model_validate_json(fixture_path.read_text().splitlines()[0])
    bad = item.model_copy(update={"source_id": "wrong"})
    with pytest.raises(ValueError):
        repo.commit_page(
            run["run_id"], item.source_id, CollectorResult(items=[item, bad], cursor="next"), True
        )
    assert repo.one("SELECT COUNT(*) AS n FROM items")["n"] == 0
    assert (
        repo.one("SELECT cursor_after FROM source_runs WHERE source_id=?", (item.source_id,))[
            "cursor_after"
        ]
        is None
    )

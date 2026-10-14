import json
import sqlite3
import time

import pytest
from conftest import ROOT, SINCE, UNTIL
from test_research import draft_for, ready

from llm_updates.coordinator import Coordinator
from llm_updates.evaluation import evaluate
from llm_updates.models import AgentAction, ModelResult
from llm_updates.research import LimitReached, research


def test_immutable_rows_and_composite_foreign_key(repo, config, fixture_path):
    result, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)
    with pytest.raises(sqlite3.IntegrityError), repo.db:
        repo.db.execute("UPDATE versions SET normalized_text='tampered'")
    with pytest.raises(sqlite3.IntegrityError), repo.db:
        repo.db.execute("DELETE FROM evidence")
    rows = repo.all("SELECT * FROM run_items LIMIT 2")
    with pytest.raises(sqlite3.IntegrityError), repo.db:
        repo.db.execute(
            "UPDATE run_items SET version_id=? WHERE run_id=? AND item_id=?",
            (rows[1]["version_id"], result["run_id"], rows[0]["item_id"]),
        )


def test_eval_story_leakage_and_empty_slots(config, tmp_path):
    result = evaluate(ROOT / "eval/synthetic.jsonl", config)
    assert result["items"] == 60
    assert {r["split"] for r in result["days"]} == {"train", "test"}
    rows = [json.loads(line) for line in (ROOT / "eval/synthetic.jsonl").read_text().splitlines()]
    rows[0]["split"] = "test"
    target = tmp_path / "leak.jsonl"
    target.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError, match="leakage"):
        evaluate(target, config)
    one = rows[1]
    one["split"] = "test"
    target.write_text(json.dumps(one))
    result = evaluate(target, config)
    assert result["days"][0]["precision_at_5"] == 0.2
    assert result["days"][0]["returned_precision"] == 1


def test_full_shortlist_can_finalize_within_default_context(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)
    draft = draft_for(candidates[0])

    class Mock:
        def generate(self, request, timeout):
            return ModelResult(
                action=AgentAction(final=draft),
                input_tokens=100,
                output_tokens=100,
                finish_reason="completed",
            )

    assert research(repo, rid, config, candidates, Mock(), time.monotonic() + 30) == draft


def test_expired_deadline_prevents_any_model_call(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)

    class Never:
        def generate(self, *args, **kwargs):
            raise AssertionError("dispatch after deadline")

    with pytest.raises(LimitReached, match="wall_time_limit"):
        research(repo, rid, config, candidates, Never(), time.monotonic() - 1)


def test_sdk_adapter_normalizes_structured_response():
    httpx = pytest.importorskip("httpx")
    openai = pytest.importorskip("openai")
    from llm_updates.providers import OpenAIAdapter

    action = {"schema_version": "1", "tool": None, "final": {"schema_version": "1", "entries": []}}

    def handler(request):
        assert request.url.path == "/v1/responses"
        return httpx.Response(
            200,
            headers={"x-request-id": "mock-request"},
            json={
                "id": "resp_mock",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "mock-model",
                "output": [
                    {
                        "id": "msg_mock",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": json.dumps(action), "annotations": []}
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens_details": {"reasoning_tokens": 10},
                },
            },
        )

    adapter = object.__new__(OpenAIAdapter)
    adapter.client = openai.OpenAI(
        api_key="synthetic-test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = adapter.generate({"model": "mock-model", "input": "test"}, 10)
    assert result.action.final.entries == []
    assert (
        result.input_tokens == 120 and result.cached_tokens == 20 and result.reasoning_tokens == 10
    )
    assert result.request_id == "mock-request"
    adapter.client.close()

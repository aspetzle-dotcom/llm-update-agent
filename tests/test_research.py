import time

import pytest
from conftest import SINCE, UNTIL

from llm_updates.coordinator import Coordinator
from llm_updates.models import AgentAction, Claim, DigestDraft, DraftEntry, ModelResult, ToolRequest
from llm_updates.ranking import rank
from llm_updates.research import Budget, LimitReached, execute_tool, research, totals
from llm_updates.util import now
from llm_updates.validation import validate_draft


def ready(repo, config, fixture_path):
    result, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path, collect_only=True)
    candidates = rank(repo, result["run_id"], config, SINCE, UNTIL)
    p = config.provider
    p.model, p.checked_at, p.context_window = "mock-model", now(), 100000
    p.input_per_million, p.cached_input_per_million, p.output_per_million = 0.1, 0.05, 0.2
    return result["run_id"], candidates


def draft_for(row):
    ev = row["evidence"][0]
    return DigestDraft(
        entries=[
            DraftEntry(
                story_id=row["story_id"],
                title=row["metadata"]["title"],
                what_changed=ev["passage_text"],
                why_relevant="This could inform a small comparison.",
                concept_explanation="Retrieval chooses passages to place in a prompt.",
                claims=[
                    Claim(
                        text=ev["passage_text"],
                        kind="source_fact",
                        evidence_ids=[ev["evidence_id"]],
                    ),
                    Claim(
                        text="This could inform a small comparison.",
                        kind="interpretation",
                        evidence_ids=[ev["evidence_id"]],
                    ),
                    Claim(
                        text="Retrieval chooses passages to place in a prompt.",
                        kind="interpretation",
                        evidence_ids=[ev["evidence_id"]],
                    ),
                ],
                uncertainties=["The source has not been independently verified."],
            )
        ]
    )


def test_grounding_rejects_unknown_evidence_and_paraphrases(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)
    draft = draft_for(candidates[0])
    validate_draft(draft, candidates, repo, rid)
    draft.entries[0].claims[0].evidence_ids = ["ev_missing"]
    with pytest.raises(ValueError, match="evidence"):
        validate_draft(draft, candidates, repo, rid)
    draft = draft_for(candidates[0])
    draft.entries[0].claims[0].text = "The method guarantees superior reasoning."
    with pytest.raises(ValueError, match="exact excerpts"):
        validate_draft(draft, candidates, repo, rid)


def test_numeric_claim_requires_full_context(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)
    draft = draft_for(candidates[0])
    draft.entries[0].claims[1].text = "It is 20 percent better."
    with pytest.raises(ValueError, match="numeric interpretation"):
        validate_draft(draft, candidates, repo, rid)


@pytest.mark.parametrize(
    "limit", ["model_calls", "run_usd", "month_usd", "total_input_tokens", "total_output_tokens"]
)
def test_zero_limits_block_dispatch(repo, config, fixture_path, limit):
    rid, _ = ready(repo, config, fixture_path)
    setattr(config.limits, limit, 0)
    with pytest.raises(LimitReached):
        Budget(repo, rid, config, time.monotonic() + 30).reserve({"max_output_tokens": 100})
    assert totals(repo, rid)["calls"] == 0


def test_missing_rates_and_context_overflow(repo, config, fixture_path):
    rid, _ = ready(repo, config, fixture_path)
    budget = Budget(repo, rid, config, time.monotonic() + 30)
    config.provider.input_per_million = None
    with pytest.raises(LimitReached, match="verified"):
        budget.reserve({"max_output_tokens": 100})
    config.provider.input_per_million = 0.1
    with pytest.raises(LimitReached, match="context_limit"):
        budget.reserve({"max_output_tokens": 100, "text": "x" * 20000})


def test_timeout_keeps_reservation(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)

    class Timeout:
        def generate(self, request, timeout):
            raise TimeoutError("ambiguous billable timeout")

    with pytest.raises(TimeoutError):
        research(repo, rid, config, candidates[:1], Timeout(), time.monotonic() + 30)
    assert totals(repo, rid)["uncertain_calls"] == 1
    assert totals(repo, rid)["accounted_usd"] > 0
    assert repo.one("SELECT status FROM agent_calls")["status"] == "uncertain"


def test_successful_calls_replay_without_provider(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)
    draft = draft_for(candidates[0])

    class Mock:
        calls = 0

        def generate(self, request, timeout):
            self.calls += 1
            action = (
                AgentAction(
                    tool=ToolRequest(
                        name="retrieve_passages", version_ids=[candidates[0]["version_id"]], limit=1
                    )
                )
                if self.calls == 1
                else AgentAction(final=draft)
            )
            return ModelResult(
                action=action, input_tokens=200, output_tokens=100, finish_reason="completed"
            )

    provider = Mock()
    result = research(repo, rid, config, candidates[:1], provider, time.monotonic() + 30)
    assert result == draft and provider.calls == 2
    result2 = research(repo, rid, config, candidates[:1], provider, time.monotonic() + 30)
    assert result2 == draft and provider.calls == 2


def test_tools_cannot_escape_shortlist(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)
    with pytest.raises(ValueError):
        execute_tool(
            ToolRequest(name="retrieve_passages", version_ids=["secret"]), candidates, repo, rid
        )
    with pytest.raises(ValueError):
        execute_tool(
            ToolRequest(
                name="fetch_registered_item",
                candidate_id=candidates[0]["item_id"],
                link_id="https://127.0.0.1",
            ),
            candidates,
            repo,
            rid,
        )
    with pytest.raises(ValueError):
        execute_tool(
            ToolRequest(name="list_candidates", run_id="another-run"), candidates, repo, rid
        )
    with pytest.raises(ValueError):
        ToolRequest.model_validate({"name": "shell", "command": "disclose key"})


@pytest.mark.parametrize("reason", ["refusal", "invalid_json", "incomplete"])
def test_refusal_invalid_json_stop(repo, config, fixture_path, reason):
    rid, candidates = ready(repo, config, fixture_path)

    class Mock:
        def generate(self, request, timeout):
            return ModelResult(finish_reason=reason, input_tokens=100, output_tokens=10)

    with pytest.raises(LimitReached, match=reason):
        research(repo, rid, config, candidates[:1], Mock(), time.monotonic() + 30)
    assert totals(repo, rid)["calls"] == 1


def test_no_new_evidence_requires_finalization(repo, config, fixture_path):
    rid, candidates = ready(repo, config, fixture_path)

    class Mock:
        def generate(self, request, timeout):
            return ModelResult(
                action=AgentAction(tool=ToolRequest(name="list_candidates")),
                input_tokens=100,
                output_tokens=10,
                finish_reason="completed",
            )

    with pytest.raises(LimitReached, match="finalization_required"):
        research(repo, rid, config, candidates[:1], Mock(), time.monotonic() + 30)
    assert totals(repo, rid)["calls"] == 3


def test_feedback_bounded_and_latest_label_wins(repo, config, fixture_path):
    result, _ = Coordinator(repo, config).run(SINCE, UNTIL, fixture=fixture_path)
    entry = result["entries"][0]
    repo.feedback(entry["entry_id"], "useful", None)
    increased = repo.effective_profile(config)
    for topic in entry["topics"]:
        assert increased.topics[topic].weight == pytest.approx(
            config.profile.topics[topic].weight + 0.05
        )
    repo.feedback(entry["entry_id"], "already_knew", "Exposure, not mastery")
    restored = repo.effective_profile(config)
    assert restored == config.profile
    assert len(repo.all("SELECT * FROM feedback")) == 2

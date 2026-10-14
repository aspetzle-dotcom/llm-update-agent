import re

from .util import sha


def validate_evidence(repo, rid, evidence):
    found = repo.one(
        """SELECT e.*,v.normalized_text FROM evidence e
        JOIN versions v USING(version_id) JOIN run_items ri USING(version_id)
        WHERE e.evidence_id=? AND ri.run_id=?""",
        (evidence["evidence_id"], rid),
    )
    if not found:
        raise ValueError("evidence is not in this run")
    if (
        found["passage_text"] != found["normalized_text"][found["start_char"] : found["end_char"]]
        or sha(found["passage_text"]) != found["passage_hash"]
        or any(
            found[k] != evidence[k]
            for k in ("version_id", "passage_text", "start_char", "end_char")
        )
    ):
        raise ValueError("evidence span or hash mismatch")


def validate_draft(draft, candidates, repo, rid):
    by_story = {r["story_id"]: r for r in candidates}
    used = set()
    for entry in draft.entries:
        if entry.story_id not in by_story or entry.story_id in used:
            raise ValueError("unknown or duplicate story")
        used.add(entry.story_id)
        row = by_story[entry.story_id]
        evidence = {e["evidence_id"]: e for e in row["evidence"]}
        if entry.title != row["metadata"]["title"]:
            raise ValueError("title must come from stored metadata")
        facts, interpretations = [], []
        for claim in entry.claims:
            if not claim.evidence_ids or any(e not in evidence for e in claim.evidence_ids):
                raise ValueError("claim needs evidence from the selected version")
            passages = [evidence[e] for e in claim.evidence_ids]
            for p in passages:
                validate_evidence(repo, rid, p)
            text = "\n".join(p["passage_text"] for p in passages)
            if claim.kind == "source_fact":
                if not any(claim.text in p["passage_text"] for p in passages):
                    raise ValueError(
                        "source facts must be exact excerpts; paraphrases need human review"
                    )
                facts.append(claim.text)
                numbers = re.findall(r"\d+(?:[.,]\d+)*", claim.text)
                for number in numbers:
                    contexts = [c for c in claim.numeric_context if c.number == number]
                    if not contexts or not any(
                        all(
                            part and part in text
                            for part in (c.number, c.unit, c.baseline, c.conditions)
                        )
                        for c in contexts
                    ):
                        raise ValueError(
                            "numeric claim lacks number, unit, baseline, or test conditions"
                        )
            else:
                # Interpretations cannot smuggle unsupported numerical claims.
                if re.search(r"\d", claim.text):
                    raise ValueError("numeric interpretation is not permitted")
                interpretations.append(claim.text)
        if not facts or entry.what_changed != " ".join(facts):
            raise ValueError("what_changed must contain exactly the cited factual excerpts")
        for prose in (entry.why_relevant, entry.concept_explanation):
            if prose and prose not in interpretations:
                raise ValueError("all explanation prose must be an evidence-linked interpretation")
    return draft

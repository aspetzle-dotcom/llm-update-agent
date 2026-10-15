from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from .collectors import pages
from .config import Config
from .models import CollectorResult, NormalizedItem
from .network import Fetcher
from .providers import OpenAIAdapter
from .ranking import rank
from .rendering import baseline_entry, export
from .research import research, totals
from .util import dumps, now, sha, uid
from .validation import validate_evidence


class Coordinator:
    def __init__(
        self, repo, config: Config, *, fetcher_factory=Fetcher, provider_factory=OpenAIAdapter
    ):
        self.repo, self.config = repo, config.model_copy(deep=True)
        self.fetcher_factory, self.provider_factory = fetcher_factory, provider_factory

    def run(
        self,
        since,
        until,
        *,
        fixture=None,
        mode="baseline",
        top=5,
        key="manual",
        collect_only=False,
        parent=None,
        resume=None,
        ranking_mode="baseline",
    ):
        if not 1 <= top <= 5:
            raise ValueError("top must be between 1 and 5")
        if datetime.fromisoformat(since) >= datetime.fromisoformat(until):
            raise ValueError("since must precede until")
        self.repo.configure(self.config)
        if not resume:
            self.config.profile = self.repo.effective_profile(self.config)
        fixture_path = str(Path(fixture).resolve()) if fixture else None
        fixture_hash = sha(Path(fixture_path).read_bytes()) if fixture_path else None
        invocation = {
            "since": since,
            "until": until,
            "fixture": fixture_path,
            "fixture_hash": fixture_hash,
            "mode": mode,
            "top": top,
            "key": key,
            "collect_only": collect_only,
            "ranking_mode": ranking_mode,
        }
        if resume:
            run = self.repo.one("SELECT * FROM runs WHERE run_id=?", (resume,))
            if not run:
                raise ValueError("unknown run")
            prior = json.loads(run["invocation_json"])
            if prior["fixture_hash"] != fixture_hash:
                raise ValueError("fixture changed; use a new invocation")
            with self.repo.db:
                self.repo.db.execute("UPDATE runs SET attempt=attempt+1 WHERE run_id=?", (resume,))
                self.repo.event(resume, "resumed", {"attempt": run["attempt"] + 1})
        else:
            run = self.repo.start(self.config, invocation, parent)
        rid = run["run_id"]
        existing = self.repo.digest(rid)
        if existing:
            # A committed result always wins, including after export interruption.
            path = export(existing, self.config.output_dir)
            self.repo.state(rid, existing["status"], "exported")
            return existing, path
        attempt_start = time.monotonic()
        elapsed = sum(
            json.loads(e["details_json"])["active_seconds"]
            for e in self.repo.all(
                "SELECT details_json FROM run_events WHERE run_id=? AND event_type='attempt_finished'",
                (rid,),
            )
        )
        deadline = attempt_start + max(0, self.config.limits.wall_seconds - elapsed)
        try:
            self.repo.state(rid, "collecting", "collecting")
            self.collect(rid, since, until, fixture_path, deadline)
            self.repo.resolve_relationships(rid)
            coverage = self.repo.coverage(rid)
            status = "succeeded" if coverage["succeeded"] == coverage["checked"] else "partial"
            if collect_only:
                self.repo.state(rid, status, "collected", coverage)
                return {"run_id": rid, "status": status, "coverage": coverage}, None
            self.repo.state(rid, "ranking", "ranking")
            snapshot = self.repo.one(
                "SELECT candidates_json FROM run_snapshots WHERE run_id=?", (rid,)
            )
            if snapshot:
                candidates = json.loads(snapshot["candidates_json"])
            else:
                candidates = rank(self.repo, rid, self.config, since, until, ranking_mode)
                with self.repo.db:
                    self.repo.db.execute(
                        "INSERT INTO run_snapshots VALUES(?,?)", (rid, dumps(candidates))
                    )
            with self.repo.db:
                rows = self.repo.all(
                    "SELECT version_id FROM run_items WHERE run_id=? ORDER BY version_id", (rid,)
                )
                self.repo.db.execute(
                    "UPDATE runs SET corpus_hash=? WHERE run_id=?", (sha(rows), rid)
                )
            digest_id = uid("digest", rid)
            entries = [baseline_entry(r, digest_id) for r in candidates[:top]]
            limitations = []
            actual_mode = "baseline"
            if mode == "research" and candidates:
                self.repo.state(rid, "researching", "researching")
                try:
                    draft = research(
                        self.repo,
                        rid,
                        self.config,
                        candidates,
                        self.provider_factory(),
                        deadline,
                        top,
                    )
                    by_story = {r["story_id"]: r for r in candidates}
                    entries = []
                    for entry in draft.entries:
                        row = by_story[entry.story_id]
                        result = baseline_entry(row, digest_id)
                        result.update(entry.model_dump())
                        eids = {eid for c in entry.claims for eid in c.evidence_ids}
                        result["evidence"] = [
                            e for e in row["evidence"] if e["evidence_id"] in eids
                        ]
                        entries.append(result)
                    actual_mode = "research"
                except Exception as exc:
                    # Error class only: SDK exception strings can contain source data or request headers.
                    code = str(exc) if type(exc).__name__ == "LimitReached" else type(exc).__name__
                    limitations.append(
                        "Model layer unavailable or invalid; baseline fallback: " + code
                    )
                    status = "partial"
                    with self.repo.db:
                        self.repo.event(rid, "model_fallback", {"error_code": code})
            if fixture_path:
                limitations.append(
                    "Synthetic offline fixtures; these are fictional updates, not current news."
                )
            if actual_mode == "research":
                limitations.append(
                    "Experimental model layer; source excerpts are validated, interpretations require human judgment."
                )
            if not entries:
                limitations.append(
                    "No new eligible selections; source status distinguishes empty coverage from an outage."
                )
            self.repo.state(rid, "validating", "validating")
            for entry in entries:
                for evidence in entry["evidence"]:
                    validate_evidence(self.repo, rid, evidence)
            payload = {
                "schema_version": "1",
                "digest_id": digest_id,
                "run_id": rid,
                "status": status,
                "mode": actual_mode,
                "requested_mode": mode,
                "window": {"since": since, "until": until},
                "source_set_version": self.config.source_set_version,
                "coverage": coverage,
                "entries": entries,
                "cost": totals(self.repo, rid),
                "limitations": limitations,
            }
            self.repo.save_digest(payload)
            path = export(payload, self.config.output_dir)
            self.repo.state(
                rid, status, "exported", {"digest_id": digest_id, "entries": len(entries)}
            )
            return payload, path
        except KeyboardInterrupt:
            self.repo.state(rid, "interrupted", outcome={"reason": "keyboard_interrupt"})
            raise
        except BaseException as exc:
            self.repo.state(rid, "failed", outcome={"error_code": type(exc).__name__})
            raise
        finally:
            with self.repo.db:
                self.repo.event(
                    rid, "attempt_finished", {"active_seconds": time.monotonic() - attempt_start}
                )

    def collect(self, rid, since, until, fixture, deadline):
        records = []
        if fixture:
            records = [
                NormalizedItem.model_validate_json(line)
                for line in Path(fixture).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            allowed = {s.source_id: s.kind for s in self.config.sources if s.enabled}
            if any(r.source_id not in allowed or r.kind != allowed[r.source_id] for r in records):
                raise ValueError("fixture contains a disabled, unknown, or mismatched source")
        for source in self.config.sources:
            if not source.enabled:
                continue
            state = self.repo.one(
                "SELECT * FROM source_runs WHERE run_id=? AND source_id=?", (rid, source.source_id)
            )
            if state["status"] in ("succeeded", "incomplete"):
                continue
            try:
                if time.monotonic() >= deadline:
                    raise TimeoutError("wall limit")
                if fixture:
                    selected = [r for r in records if r.source_id == source.source_id]
                    capped = len(selected) > self.config.limits.candidates_per_source
                    self.repo.commit_page(
                        rid,
                        source.source_id,
                        CollectorResult(
                            items=selected[: self.config.limits.candidates_per_source],
                            complete=not capped,
                            warnings=["candidate cap reached"] if capped else [],
                        ),
                        True,
                    )
                    continue
                source_deadline = min(
                    deadline, time.monotonic() + self.config.limits.source_timeout_seconds
                )
                fetcher = self.fetcher_factory(self.config, self.repo, source_deadline)
                source_since = state["window_since"]
                source_until = state["window_until"] or until
                if source_since is None:
                    source_since = since
                    # Only reuse coverage that includes the requested start. A
                    # disjoint historical collection cannot establish a watermark.
                    last = self.repo.one(
                        """SELECT MAX(window_until) AS covered_until FROM source_runs
                        WHERE source_id=? AND status='succeeded' AND run_id!=?
                        AND window_since<=? AND window_until>?""",
                        (source.source_id, rid, since, since),
                    )
                    if last["covered_until"]:
                        overlap = datetime.fromisoformat(last["covered_until"]) - timedelta(
                            hours=self.config.limits.overlap_hours
                        )
                        if datetime.fromisoformat(since) < overlap < datetime.fromisoformat(until):
                            source_since = overlap.isoformat()
                    with self.repo.db:
                        self.repo.db.execute(
                            "UPDATE source_runs SET window_since=?,window_until=? WHERE run_id=? AND source_id=?",
                            (source_since, source_until, rid, source.source_id),
                        )
                for result, terminal in pages(
                    source,
                    fetcher,
                    source_since,
                    source_until,
                    self.config.limits.candidates_per_source,
                    state["cursor_after"],
                    state["fetched_count"],
                ):
                    self.repo.commit_page(rid, source.source_id, result, terminal)
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                with self.repo.db:
                    self.repo.db.execute(
                        """UPDATE source_runs SET status='failed',error_code=?,
                        last_attempt_at=? WHERE run_id=? AND source_id=?""",
                        (code, now(), rid, source.source_id),
                    )
                    self.repo.event(rid, "source_failure", {"error_code": code}, source.source_id)


def resume_run(repo, rid, *, provider_factory=OpenAIAdapter):
    row = repo.one("SELECT * FROM runs WHERE run_id=?", (rid,))
    if not row:
        raise ValueError("unknown run")
    invocation = json.loads(row["invocation_json"])
    config = Config.model_validate(invocation.pop("config_snapshot"))
    invocation.pop("fixture_hash")
    return Coordinator(repo, config, provider_factory=provider_factory).run(
        **invocation, resume=rid
    )

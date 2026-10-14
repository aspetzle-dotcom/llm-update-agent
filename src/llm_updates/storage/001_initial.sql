CREATE TABLE sources (
 source_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('arxiv','github','lab','hf')),
 name TEXT NOT NULL, endpoint TEXT NOT NULL UNIQUE, policy_json TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), cursor TEXT, last_success_at TEXT, next_allowed_at TEXT
);
CREATE TABLE runs (
 run_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, parent_run_id TEXT REFERENCES runs,
 started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL, stage TEXT NOT NULL,
 config_hash TEXT NOT NULL, profile_hash TEXT NOT NULL, corpus_hash TEXT NOT NULL,
 schema_version TEXT NOT NULL, budget_json TEXT NOT NULL, outcome_json TEXT NOT NULL,
 invocation_json TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX runs_time ON runs(started_at,status);
CREATE TABLE items (
 item_id TEXT PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 canonical_url TEXT NOT NULL, title TEXT NOT NULL, first_seen_at TEXT NOT NULL, published_at TEXT,
 date_basis TEXT NOT NULL CHECK(date_basis IN ('publisher','first_seen','unknown')),
 status TEXT NOT NULL CHECK(status IN ('active','withdrawn'))
);
CREATE INDEX items_published ON items(published_at);
CREATE TABLE item_aliases (
 alias_key TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items,
 source_id TEXT NOT NULL REFERENCES sources, external_id TEXT NOT NULL, observed_url TEXT NOT NULL,
 UNIQUE(source_id,external_id)
);
CREATE INDEX aliases_item ON item_aliases(item_id);
CREATE TABLE versions (
 version_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items, publisher_version TEXT,
 content_hash TEXT NOT NULL, raw_hash TEXT NOT NULL, normalized_text TEXT NOT NULL,
 extractor_version TEXT NOT NULL, fetched_at TEXT NOT NULL, metadata_json TEXT NOT NULL,
 snapshot_ref TEXT, first_run_id TEXT NOT NULL REFERENCES runs,
 UNIQUE(item_id,content_hash), UNIQUE(item_id,version_id)
);
CREATE INDEX versions_item_time ON versions(item_id,fetched_at);
CREATE TABLE evidence (
 evidence_id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions,
 start_char INTEGER NOT NULL, end_char INTEGER NOT NULL, passage_text TEXT NOT NULL,
 locator TEXT NOT NULL, passage_hash TEXT NOT NULL,
 CHECK(start_char >= 0 AND end_char > start_char), UNIQUE(version_id,start_char,end_char)
);
CREATE INDEX evidence_version ON evidence(version_id);
CREATE TABLE stories (story_id TEXT PRIMARY KEY, label TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE story_members (
 story_id TEXT NOT NULL REFERENCES stories, item_id TEXT NOT NULL UNIQUE REFERENCES items,
 method TEXT NOT NULL, confidence REAL NOT NULL, PRIMARY KEY(story_id,item_id)
);
CREATE TABLE source_runs (
 run_id TEXT NOT NULL REFERENCES runs, source_id TEXT NOT NULL REFERENCES sources,
 status TEXT NOT NULL, cursor_before TEXT, cursor_after TEXT, fetched_count INTEGER NOT NULL DEFAULT 0,
 error_code TEXT, last_attempt_at TEXT NOT NULL, warnings_json TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(run_id,source_id)
);
CREATE TABLE run_items (
 run_id TEXT NOT NULL REFERENCES runs, item_id TEXT NOT NULL REFERENCES items,
 version_id TEXT NOT NULL, story_id TEXT NOT NULL REFERENCES stories, stage TEXT NOT NULL,
 relevance REAL NOT NULL DEFAULT 0, recency REAL NOT NULL DEFAULT 0,
 novelty REAL NOT NULL DEFAULT 0, final_score REAL NOT NULL DEFAULT 0,
 exclusion_reason TEXT, score_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(run_id,item_id),
 FOREIGN KEY(item_id,version_id) REFERENCES versions(item_id,version_id)
);
CREATE INDEX run_scores ON run_items(run_id,final_score DESC);
CREATE TABLE agent_calls (
 call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs, sequence INTEGER NOT NULL,
 created_at TEXT NOT NULL, request_hash TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
 status TEXT NOT NULL, input_tokens INTEGER NOT NULL CHECK(input_tokens>=0),
 output_tokens INTEGER NOT NULL CHECK(output_tokens>=0),
 reserved_cost REAL NOT NULL CHECK(reserved_cost>=0), actual_cost REAL CHECK(actual_cost>=0),
 latency_ms INTEGER NOT NULL, request_json TEXT NOT NULL, response_json TEXT,
 metadata_json TEXT NOT NULL, UNIQUE(run_id,sequence)
);
CREATE TABLE digests (
 digest_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs,
 status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE digest_entries (
 entry_id TEXT PRIMARY KEY, digest_id TEXT NOT NULL REFERENCES digests,
 story_id TEXT NOT NULL REFERENCES stories, position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 5),
 text_json TEXT NOT NULL, UNIQUE(digest_id,story_id), UNIQUE(digest_id,position)
);
CREATE TABLE claims (
 claim_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES digest_entries, text TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('source_fact','interpretation')), support_status TEXT NOT NULL
);
CREATE TABLE claim_evidence (
 claim_id TEXT NOT NULL REFERENCES claims, evidence_id TEXT NOT NULL REFERENCES evidence,
 relation TEXT NOT NULL CHECK(relation IN ('supports','contradicts')), PRIMARY KEY(claim_id,evidence_id)
);
CREATE TABLE feedback (
 feedback_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES digest_entries,
 label TEXT NOT NULL CHECK(label IN ('useful','already_knew','skip')), note TEXT, created_at TEXT NOT NULL
);
CREATE TABLE profiles (
 profile_id TEXT PRIMARY KEY, version INTEGER NOT NULL UNIQUE, settings_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE run_events (
 event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs, created_at TEXT NOT NULL,
 event_type TEXT NOT NULL, entity_id TEXT, details_json TEXT NOT NULL
);
CREATE INDEX events_run_time ON run_events(run_id,created_at);
CREATE TABLE http_cache (
 url TEXT PRIMARY KEY, etag TEXT, modified TEXT, body BLOB NOT NULL,
 headers_json TEXT NOT NULL, fetched_at TEXT NOT NULL
);

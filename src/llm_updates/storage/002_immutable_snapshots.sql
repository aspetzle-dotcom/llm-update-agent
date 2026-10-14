CREATE TABLE run_snapshots (
 run_id TEXT PRIMARY KEY REFERENCES runs,
 candidates_json TEXT NOT NULL
);
CREATE TRIGGER immutable_versions_update BEFORE UPDATE ON versions
BEGIN SELECT RAISE(ABORT,'versions are immutable'); END;
CREATE TRIGGER immutable_versions_delete BEFORE DELETE ON versions
BEGIN SELECT RAISE(ABORT,'versions are retained'); END;
CREATE TRIGGER immutable_evidence_update BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT,'evidence is immutable'); END;
CREATE TRIGGER immutable_evidence_delete BEFORE DELETE ON evidence
BEGIN SELECT RAISE(ABORT,'cited evidence is retained'); END;

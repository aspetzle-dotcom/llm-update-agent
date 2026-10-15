ALTER TABLE source_runs ADD COLUMN window_since TEXT;
ALTER TABLE source_runs ADD COLUMN window_until TEXT;
-- Legacy cursors did not save their effective query window, and clock-time
-- watermarks cannot establish coverage. Recollect unfinished legacy sources.
UPDATE source_runs SET cursor_after=NULL, fetched_count=0, status='pending'
WHERE status NOT IN ('succeeded','incomplete');
UPDATE sources SET last_success_at=NULL;

CREATE TABLE current_versions (
 item_id TEXT PRIMARY KEY REFERENCES items,
 version_id TEXT NOT NULL,
 FOREIGN KEY(item_id,version_id) REFERENCES versions(item_id,version_id)
);
INSERT INTO current_versions
SELECT item_id,version_id FROM versions v
WHERE v.rowid=(SELECT MAX(v2.rowid) FROM versions v2 WHERE v2.item_id=v.item_id);

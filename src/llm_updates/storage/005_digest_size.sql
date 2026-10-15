-- Allow up to ten entries per digest. SQLite cannot alter a CHECK constraint, so the
-- table is rebuilt; the caller disables foreign keys around this script and verifies
-- them afterwards. Entry ids, positions and text are copied unchanged.
CREATE TABLE digest_entries_new (
 entry_id TEXT PRIMARY KEY, digest_id TEXT NOT NULL REFERENCES digests,
 story_id TEXT NOT NULL REFERENCES stories, position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 10),
 text_json TEXT NOT NULL, UNIQUE(digest_id,story_id), UNIQUE(digest_id,position)
);
INSERT INTO digest_entries_new SELECT entry_id,digest_id,story_id,position,text_json FROM digest_entries;
DROP TABLE digest_entries;
ALTER TABLE digest_entries_new RENAME TO digest_entries;

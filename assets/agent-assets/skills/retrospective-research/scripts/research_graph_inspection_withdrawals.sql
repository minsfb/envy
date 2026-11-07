-- Storage revision 2; the v1 packet protocol and base schema remain unchanged.
-- Execute only in the transaction admitting its schema_migrations event.
CREATE TABLE inspection_withdrawals (
    withdrawal_id TEXT PRIMARY KEY NOT NULL,
    inspection_id TEXT NOT NULL UNIQUE REFERENCES inspections(inspection_id),
    actor_id TEXT NOT NULL REFERENCES actors(actor_id),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    evidence_path TEXT NOT NULL CHECK (length(trim(evidence_path)) > 0),
    evidence_sha256 TEXT NOT NULL CHECK (length(evidence_sha256) = 64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
    withdrawn_at TEXT NOT NULL,
    created_batch_id TEXT NOT NULL REFERENCES ingest_batches(batch_id)
);
CREATE TRIGGER inspection_withdrawals_no_update BEFORE UPDATE ON inspection_withdrawals BEGIN
    SELECT RAISE(ABORT, 'inspection_withdrawals is append-only');
END;
CREATE TRIGGER inspection_withdrawals_no_delete BEFORE DELETE ON inspection_withdrawals BEGIN
    SELECT RAISE(ABORT, 'inspection_withdrawals is append-only');
END;
CREATE VIEW effective_inspections AS
SELECT i.* FROM inspections i
WHERE NOT EXISTS (SELECT 1 FROM inspection_withdrawals w WHERE w.inspection_id = i.inspection_id);
DROP VIEW sufficient_inspection;
CREATE VIEW sufficient_inspection AS
SELECT c.canonical_id AS artifact_id, s.question_id, s.required_scope_key, s.format_policy_sha256,
       i.inspection_id, i.return_id
FROM inspection_scopes s JOIN effective_inspections i USING (inspection_id)
JOIN reader_returns r USING (return_id)
JOIN canonical_artifacts c ON c.artifact_id = i.artifact_id
WHERE s.coverage = 'sufficient' AND i.provenance_basis = 'native' AND r.verification_state = 'verified'
  AND EXISTS (SELECT 1 FROM inspection_captures ic WHERE ic.inspection_id = i.inspection_id);
PRAGMA user_version = 2;

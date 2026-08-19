-- Double-entry prevention: file hashes + transaction dedup
--
-- Prevents the same bank statement being imported twice and prevents
-- duplicate transactions within the same entity.

-- 1. File hash on imports — SHA-256 of the uploaded file content.
--    If the same file is uploaded again, the API rejects it.
ALTER TABLE imports ADD COLUMN IF NOT EXISTS file_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_imports_file_hash_org
  ON imports (org_id, entity_id, file_hash)
  WHERE file_hash IS NOT NULL;

COMMENT ON COLUMN imports.file_hash IS
'SHA-256 hash of the uploaded file content. Prevents the same statement '
'being imported twice for the same entity. NULL only for legacy rows.';

-- 2. Transaction fingerprint — prevents duplicate rows within an entity.
--    A transaction is unique per entity by (date, amount, direction, description).
--    This catches re-uploads even if the file_hash differs (e.g. re-exported
--    CSV with same transactions but different filename/metadata).
CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_fingerprint
  ON transactions (org_id, entity_id, date, amount, direction, description);

COMMENT ON INDEX uq_transactions_fingerprint IS
'Prevents duplicate transactions per entity. A row is unique by '
(org_id, entity_id, date, amount, direction, description). '
Catches re-uploads of the same statement even with different file hashes.';
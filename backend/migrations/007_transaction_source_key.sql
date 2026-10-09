-- Stable identity for imported purchases. Manual entries stay NULL.
-- Existing rows start NULL; insert/merge backfills matching legacy rows
-- when the same charge is imported again so re-merges do not double-count.
ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS source_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_user_source_key
ON transactions (user_id, source_key)
WHERE source_key IS NOT NULL;

-- Stable identity for imported purchases. Manual entries stay NULL.
-- Existing rows are not backfilled; that is a separate migration.
ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS source_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_user_source_key
ON transactions (user_id, source_key)
WHERE source_key IS NOT NULL;

-- Stable identity for imported purchases. Manual entries stay NULL.
-- Pre-existing dashboard rows keep a null key until a later import of the
-- same charge attaches the stable key and avoids duplicates.
ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS source_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_user_source_key
ON transactions (user_id, source_key)
WHERE source_key IS NOT NULL;

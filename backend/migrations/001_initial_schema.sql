CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS statement_uploads (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    original_file_name TEXT NOT NULL,
    stored_file_name TEXT NOT NULL,
    file_fingerprint_sha256 TEXT NOT NULL,
    bank_name TEXT,
    statement_period_start TEXT,
    statement_period_end TEXT,
    currency TEXT,
    parse_status TEXT NOT NULL,
    parse_reason TEXT,
    parse_error TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    status TEXT NOT NULL,
    request_id TEXT,
    ip_address TEXT,
    user_agent TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    file_name TEXT NOT NULL,
    original_file_name TEXT
);

ALTER TABLE transactions
ALTER COLUMN date TYPE TEXT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS original_file_name TEXT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS category TEXT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS file_fingerprint TEXT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS payment_method TEXT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'USD';

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS user_id BIGINT;

ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS statement_upload_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_transactions_file_fingerprint
ON transactions (file_fingerprint);

CREATE INDEX IF NOT EXISTS idx_transactions_user_uploaded_at
ON transactions (user_id, uploaded_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_transactions_user_date
ON transactions (user_id, date);

CREATE INDEX IF NOT EXISTS idx_transactions_statement_upload
ON transactions (statement_upload_id);

CREATE INDEX IF NOT EXISTS idx_statement_uploads_user_uploaded_at
ON statement_uploads (user_id, uploaded_at DESC, id DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_statement_uploads_user_fingerprint
ON statement_uploads (user_id, file_fingerprint_sha256);

CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash
ON user_sessions (token_hash);

CREATE INDEX IF NOT EXISTS idx_audit_logs_user_occurred_at
ON audit_logs (user_id, occurred_at DESC, id DESC);

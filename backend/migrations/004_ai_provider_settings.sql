CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS user_ai_provider_settings (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    encrypted_api_key BYTEA NOT NULL,
    api_key_last4 TEXT NOT NULL,
    model TEXT,
    is_selected BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT user_ai_provider_settings_provider_check
        CHECK (provider IN ('openai', 'perplexity')),
    CONSTRAINT user_ai_provider_settings_user_provider_unique
        UNIQUE (user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_user_ai_provider_settings_user_selected
ON user_ai_provider_settings (user_id, is_selected, updated_at DESC, id DESC);

-- Microsoft Entra sign-in (#100). Additive only: no existing row changes.
--
-- One row per external identity. The first sign-in of an allowed identity
-- creates a new user; it never attaches to an existing local user, even when
-- the email or username matches.
CREATE TABLE IF NOT EXISTS user_identities (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    tenant_id TEXT,
    object_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    UNIQUE (provider, issuer, subject)
);

CREATE INDEX IF NOT EXISTS idx_user_identities_user_id
ON user_identities (user_id);

-- Short-lived OIDC login transactions, deleted when used. browser_binding_hash
-- is the SHA-256 of a random HttpOnly cookie set when the login starts, so a
-- callback URL cannot finish a login that a different browser started.
CREATE TABLE IF NOT EXISTS oidc_login_states (
    state_hash TEXT PRIMARY KEY,
    browser_binding_hash TEXT NOT NULL,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    return_to TEXT NOT NULL DEFAULT '/',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oidc_login_states_expires_at
ON oidc_login_states (expires_at);

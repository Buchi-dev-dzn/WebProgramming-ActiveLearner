CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    stock INTEGER NOT NULL CHECK (stock >= 0),
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    tag TEXT NOT NULL DEFAULT '',
    image_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT products_sku_format CHECK (sku ~ '^[A-Za-z0-9._-]+$')
);

CREATE INDEX IF NOT EXISTS idx_products_sku
ON products (sku);

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email_lookup_hash BYTEA NOT NULL UNIQUE,
    email_ciphertext BYTEA NOT NULL,
    email_nonce BYTEA NOT NULL,
    email_key_id TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_algorithm TEXT NOT NULL DEFAULT 'pbkdf2_hmac_sha256',
    password_iterations INTEGER NOT NULL DEFAULT 600000,
    role TEXT NOT NULL DEFAULT 'member',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    CONSTRAINT users_role_allowed CHECK (role IN ('member', 'customer', 'seller', 'admin', 'support')),
    CONSTRAINT users_password_algorithm_allowed CHECK (password_algorithm = 'pbkdf2_hmac_sha256'),
    CONSTRAINT users_password_iterations_min CHECK (password_iterations >= 600000)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lookup_hash
ON users (email_lookup_hash);

ALTER TABLE products
ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES users(id);

CREATE INDEX IF NOT EXISTS idx_products_owner_user_id
ON products (owner_user_id);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash BYTEA NOT NULL UNIQUE,
    family_id UUID NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    replaced_by_token_hash BYTEA,
    request_id TEXT,
    source_ip_hash BYTEA,
    user_agent_summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id
ON refresh_tokens (user_id);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family_id
ON refresh_tokens (family_id);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at
ON refresh_tokens (expires_at);

CREATE TABLE IF NOT EXISTS seller_profiles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE REFERENCES users(id),
    store_name TEXT NOT NULL,
    store_description TEXT NOT NULL DEFAULT '',
    business_email_lookup_hash BYTEA,
    business_email_ciphertext BYTEA,
    business_email_nonce BYTEA,
    business_email_key_id TEXT,
    phone_lookup_hash BYTEA,
    phone_ciphertext BYTEA,
    phone_nonce BYTEA,
    phone_key_id TEXT,
    business_address_ciphertext BYTEA,
    business_address_nonce BYTEA,
    business_address_key_id TEXT,
    verification_status TEXT NOT NULL DEFAULT 'pending',
    payout_account_token_ciphertext BYTEA,
    payout_account_token_nonce BYTEA,
    payout_account_token_key_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT seller_profiles_verification_status_allowed CHECK (
        verification_status IN ('pending', 'verified', 'rejected', 'suspended')
    )
);

CREATE INDEX IF NOT EXISTS idx_seller_profiles_user_id
ON seller_profiles (user_id);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id BIGINT REFERENCES users(id),
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    request_id TEXT,
    source_ip_hash BYTEA,
    user_agent_summary TEXT,
    severity TEXT NOT NULL DEFAULT 'info',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_action
ON audit_events (action);

CREATE INDEX IF NOT EXISTS idx_audit_events_actor_user_id
ON audit_events (actor_user_id);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
ON audit_events (created_at);

GRANT CONNECT ON DATABASE app_db TO app_user;
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE ON products TO app_user;
GRANT USAGE, SELECT ON SEQUENCE products_id_seq TO app_user;
GRANT SELECT, INSERT, UPDATE ON users TO app_user;
GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO app_user;
GRANT SELECT, INSERT, UPDATE ON refresh_tokens TO app_user;
GRANT USAGE, SELECT ON SEQUENCE refresh_tokens_id_seq TO app_user;
GRANT SELECT, INSERT, UPDATE ON seller_profiles TO app_user;
GRANT USAGE, SELECT ON SEQUENCE seller_profiles_id_seq TO app_user;
GRANT SELECT, INSERT ON audit_events TO app_user;
GRANT USAGE, SELECT ON SEQUENCE audit_events_id_seq TO app_user;

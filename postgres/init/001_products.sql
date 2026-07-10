DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE rolname = 'app_user'
    ) THEN
        CREATE ROLE app_user
        LOGIN
        PASSWORD 'app_password'
        NOSUPERUSER
        NOCREATEDB
        NOCREATEROLE;
    ELSE
        ALTER ROLE app_user
        WITH LOGIN
        PASSWORD 'app_password'
        NOSUPERUSER
        NOCREATEDB
        NOCREATEROLE;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    stock INTEGER NOT NULL CHECK (stock >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT products_sku_format CHECK (sku ~ '^[A-Za-z0-9._-]+$')
);

CREATE INDEX IF NOT EXISTS idx_products_sku
ON products (sku);

GRANT CONNECT ON DATABASE app_db TO app_user;
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE ON products TO app_user;
GRANT USAGE, SELECT ON SEQUENCE products_id_seq TO app_user;

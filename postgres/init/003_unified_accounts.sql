-- Migrate legacy buyer/seller accounts without touching refresh tokens.
-- Existing access tokens remain valid because authorization resolves the user from DB.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_allowed;

ALTER TABLE users ALTER COLUMN role SET DEFAULT 'member';

UPDATE users
SET role = 'member', updated_at = now()
WHERE role IN ('customer', 'seller');

ALTER TABLE users
ADD CONSTRAINT users_role_allowed
CHECK (role IN ('member', 'admin', 'support'));

ALTER TABLE products
ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES users(id);

CREATE INDEX IF NOT EXISTS idx_products_owner_user_id
ON products (owner_user_id);

GRANT SELECT, INSERT, UPDATE ON products TO app_user;

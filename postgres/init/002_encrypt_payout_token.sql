-- Existing installations only: add encrypted payout-token storage.
-- Apply this before deploying the FastAPI version that writes these columns.
ALTER TABLE seller_profiles
    ADD COLUMN IF NOT EXISTS payout_account_token_ciphertext BYTEA,
    ADD COLUMN IF NOT EXISTS payout_account_token_nonce BYTEA,
    ADD COLUMN IF NOT EXISTS payout_account_token_key_id TEXT;

-- The old plaintext column is intentionally not migrated in SQL because the
-- AES key belongs to the application, not PostgreSQL. Re-submit an existing
-- token through POST /api/seller/profile, verify the ciphertext, then remove
-- the legacy column with the command documented in AUTH_CRYPTO_DESIGN.md.

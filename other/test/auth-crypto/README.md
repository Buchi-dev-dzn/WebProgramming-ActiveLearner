# Auth Crypto Test

> 実装状況: 2026-08-06 時点。`check_auth_crypto.py --help` とスクリプト内の検証ケースを正本とします。テストはユーザーと監査データを DB に作成します。

このディレクトリには、認証・暗号化・出品者プロフィール基盤を検証するためのテストを置きます。

## 目的

- ユーザー登録ができるか
- 同じ email を重複登録できないか
- 誤 password を `401` で拒否するか
- 正しい password で login し、access token と refresh token を返すか
- refresh token をローテーションし、古い refresh token を拒否するか
- refresh / logout がCSRFトークンなしでは拒否されるか
- refresh token再利用時に同じfamilyが失効するか
- Bearer token 付きで `/api/auth/me` が通るか
- Bearer token 付きで `/api/auth/audit-events` が通るか
- token なしの `/api/auth/me` を `401` で拒否するか
- seller ユーザーが出品者プロフィールを作成・取得できるか
- logout で refresh token を失効できるか
- 任意で DB 内に平文 email / password / refresh token / payout account token が保存されていないか確認する

## スクリプト

- [check_auth_crypto.py](/home/buchi/WebProgramming-ActiveLearner/other/test/auth-crypto/check_auth_crypto.py)

## 使い方

公開入口経由で API だけ確認する:

```bash
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1
```

JSON で出す:

```bash
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1 --json
```

DB 内の保存状態まで確認する:

```bash
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1 --check-db --compose-dir /home/buchi/WebProgramming-ActiveLearner
```

既存行を値を表示せずに実測する:

```bash
docker compose exec -T postgres psql -U postgres -d app_db -P pager=off -c "
SELECT
  count(*) AS profiles,
  count(*) FILTER (WHERE payout_account_token_ciphertext IS NOT NULL) AS encrypted_tokens,
  count(*) FILTER (
    WHERE payout_account_token_ciphertext IS NOT NULL
      AND octet_length(payout_account_token_nonce) = 12
      AND payout_account_token_key_id IS NOT NULL
  ) AS structurally_valid_tokens
FROM seller_profiles;"
```

旧カラムが移行中のDBに残っている場合は、平文値を表示せず件数だけ確認します。

```bash
docker compose exec -T postgres psql -U postgres -d app_db -P pager=off -c "
SELECT count(*) AS legacy_plaintext_rows
FROM seller_profiles
WHERE payout_account_token IS NOT NULL;"
```

Docker API を使わず、直接 `psql` で到達できる環境では次も使えます。

```bash
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1 --check-db --db-mode psql --psql-dsn "$DATABASE_URL"
```

ただし現在の Compose では PostgreSQL をホスト公開していないため、通常は `--db-mode docker` を使います。

## 期待値

- `register_unified_account`
  - `201`
  - role なしで登録し、email と `roles=["buyer", "seller"]` を返す
- `duplicate_email_rejected`
  - `409`
  - 大文字小文字を変えた同一 email も重複として扱う
- `bad_password_rejected`
  - `401`
- `login_issues_jwt`
  - `200`
  - `access_token` が JWT 形式で返り、`refresh_token` も返る
- `auth_me_accepts_bearer_token`
  - `200`
  - `X-Request-Id` が本文に伝播する
- `refresh_rotates_token`
  - `200`
  - 新しい access token と refresh token を返す
- `old_refresh_token_rejected`
  - `401`
  - ローテーション済みの古い refresh token は使えない
- `auth_refresh_reuse_detected`
  - 再利用検知時に同一familyの未失効refresh tokenも失効する
- `own_audit_events_visible`
  - `200`
  - `auth_register`, `auth_login` など本人の監査イベントを返す
- `auth_me_requires_token`
  - `401`
- `seller_profile_upsert`
  - `200`
  - 出品者プロフィールを作成または更新する
- `seller_profile_get`
  - `200`
  - business email を復号した値として返す
  - payout account token 自体は返さず、`has_payout_account_token=true` を返す
- `logout_revokes_refresh_token`
  - `200`
  - refresh token を失効する
- `db_plaintext_inspection`
  - `--check-db` 指定時は DB 内の `email_ciphertext`, `email_lookup_hash`, `password_hash`, `refresh_tokens.token_hash` に平文 email / password / refresh token が含まれないことと、`audit_events` にイベントが残ることを見る

成功時の DB 内部検査出力例:

```text
db_plaintext_inspection status=checked matched=yes f|f|f|t|t|t|t
```

意味:

- 1つ目 `f`
  - `email_ciphertext` に平文 email が含まれない
- 2つ目 `f`
  - `email_lookup_hash` に平文 email が含まれない
- 3つ目 `f`
  - `password_hash` に平文 password が含まれない
- 4つ目 `t`
  - `password_hash` が `pbkdf2_sha256$600000$...` 形式である
- 5つ目 `t`
  - `refresh_tokens.token_hash` に平文 refresh token が含まれない
- 6つ目 `t`
  - `audit_events` に認証系イベントが残っている
- 7つ目 `t`
  - payout account token の暗号文・nonce・key id が保存され、暗号文に平文 token が含まれない

## 注意

- このテストは毎回ランダムな email を作成するため、DB にテストユーザーが残ります
- `--check-db` は `docker compose exec postgres` を使うため、Docker API にアクセスできる環境で実行してください
- HTTPS は自己署名証明書のため、スクリプト内で証明書検証を無効化しています
- DB 保存確認は最低限の平文混入チェックです。暗号強度の証明ではなく、保存方針の実装ミスを見つけるための検査です

## よくある失敗

### すべて nginx の HTML 404 になる

例:

```text
<html>
<head><title>404 Not Found</title></head>
...
<center>nginx</center>
```

この場合、FastAPI の認証 API まで届いていません。多くの場合、稼働中の `waf` コンテナが古い設定のままで、`/api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout`, `/api/auth/me`, `/api/auth/audit-events`, `/api/seller/profile` を許可していません。

Linux VM 側で次を実行します。

```bash
cd /home/buchi/WebProgramming-ActiveLearner
docker compose up -d --build --force-recreate fastapi-app waf
docker compose exec -T postgres psql -U postgres -d app_db < postgres/init/001_products.sql
```

既存 DB が過去の `app_user` 管理ユーザーで作成されている場合は、存在する管理ユーザーで SQL を適用してください。

反映確認:

```bash
docker compose exec waf nginx -T | grep -E 'api/auth|api/seller'
curl -k -i https://127.0.0.1/api/auth/me
```

期待値:

```text
GET /api/auth/me without token -> 401
```

`404` のままなら、WAF 設定がまだ反映されていないか、テスト対象 IP が別の VM / 別の Compose stack を指しています。

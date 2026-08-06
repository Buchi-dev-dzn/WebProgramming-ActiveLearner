# FastAPI Application

> 実装状況: 2026-08-06 時点。エンドポイントの正本は `app/main.py` です。

このディレクトリには、内部 API サービスとして動く `fastapi-app` の実装を置きます。

## 役割

- `internal-firewall` の後段で業務 API を返す
- PostgreSQL への疎通状態を `/api/health` で返す
- PostgreSQL に対する商品データの作成・取得・更新を行う
- PostgreSQL に対するユーザー登録、ログイン、JWT 認証、出品者プロフィール管理を行う
- `reverse-proxy` から引き継がれた `X-Request-Id` と `X-Forwarded-*` を前提に動く

## 現在のエンドポイント

- `GET /health`
  - コンテナ内直通のヘルスチェック
- `GET /api/health`
  - 外部経路の集約ヘルスチェック
- `GET /api/info`
  - 現構成の説明用レスポンス
- `GET /api/products`
  - 商品一覧を返す
- `POST /api/products`
  - 統合アカウントまたは admin が所有者として商品を作成する
- `GET /api/product?sku=...`
  - SKU 指定で商品を 1 件返す
- `POST /api/product/stock`
  - 商品所有者または admin が SKU 指定で在庫数を更新する
- `POST /api/auth/register`
  - `email` と `password` だけを受け取り、購入・出品可能な統合アカウントを登録する（`role` 入力は禁止）
- `POST /api/auth/login`
  - email の HMAC blind index でユーザー検索し、password hash を検証して access token と refresh token を返す
- `POST /api/auth/refresh`
  - refresh token を HMAC hash で照合し、古い token を失効して新しい access token / refresh token を返す
- `POST /api/auth/logout`
  - Bearer token と refresh token を確認し、refresh token を失効する
- `GET /api/auth/me`
  - Bearer token を検証して現在のユーザー情報を返す
- `GET /api/auth/audit-events`
  - Bearer token のユーザー本人に紐づく監査イベントを返す
- `POST /api/seller/profile`
  - 統合アカウント / admin の出品者プロフィールを作成または更新する
- `GET /api/seller/profile`
  - Bearer token に紐づく出品者プロフィールを返す
- `GET /api/security/audit-events`
  - admin / support ユーザー向けに監査イベントを返す
- `GET /api/security/monitoring/summary`
  - admin / support ユーザー向けに認証異常、NIDS/HIDS 相当のシグナル概要を返す
- `POST /api/internal/security-events`
  - `X-Sensor-Token` が `SECURITY_SENSOR_TOKEN` と一致する内部センサーのイベントを `audit_events` に保存する

## SQL と DB アクセスの扱い

- PostgreSQL は `db_net` にだけ所属し、ホスト側には `ports` を公開しない
- `db_net` に接続しているのは `fastapi-app` と `postgres` のみ
- 外部からは `external-firewall -> nips -> waf -> reverse-proxy -> internal-firewall -> fastapi-app` を通る必要がある
- FastAPI では `asyncpg` の `$1`, `$2` 形式の bind parameter だけで SQL を実行する
- FastAPI は DB 管理ユーザーではなく、制限付きの `app_user` で PostgreSQL に接続する
- テーブル作成は [postgres/init/001_products.sql](/home/buchi/WebProgramming-ActiveLearner/postgres/init/001_products.sql) で DB コンテナ側に分離する
- SKU は `^[A-Za-z0-9._-]+$` に制限し、不要な文字を API 入力で受け付けない
- パスワードは復号可能な暗号化ではなく、`PBKDF2-HMAC-SHA-256` の password hash として保存する
- email や出品者連絡先は `AES-256-GCM` で暗号化し、検索には `HMAC-SHA-256` の blind index を使う
- refresh token は平文保存せず、`JWT_SECRET_KEY_B64` を使った HMAC-SHA-256 の token hash として保存する
- ログイン失敗回数と一時ロック状態を `users` に保持する
- 登録、ログイン、ログイン失敗、ロック、refresh、logout、商品登録、在庫更新、出品者プロフィール更新を `audit_events` に記録する
- JWT, 暗号鍵, 平文 password, password hash, ciphertext, lookup hash はログに出さない前提で扱う
- `JWT_SECRET_KEY_B64`, `DATA_ENCRYPTION_KEY_B64`, `EMAIL_LOOKUP_KEY_B64` は学習用には Compose の environment で渡しているが、本番では secret manager などへ移す

## 今回追加した認証強化

### refresh token

- `POST /api/auth/login` は短命の access token と refresh token を返す
- refresh token は平文では保存しない
- DB には `refresh_tokens.token_hash` として HMAC-SHA-256 の hash を保存する
- `POST /api/auth/refresh` は古い refresh token を失効し、新しい access token / refresh token を返す
- ローテーション済みの古い refresh token は再利用できない
- `POST /api/auth/logout` は該当 refresh token を失効する

### login attempt / account lockout

- ログイン失敗回数は `users.failed_login_count` に保存する
- 失敗回数が閾値を超えた場合は `users.locked_until` に一時ロック期限を保存する
- ロック中のログイン試行は `423 account_locked` を返す
- 正常ログイン時は失敗回数とロック状態をリセットする

### audit_events

- `auth_register`
- `auth_login`
- `auth_login_failed`
- `auth_login_blocked`
- `auth_refresh`
- `auth_refresh_failed`
- `auth_logout`
- `seller_profile_upsert`
- `product_create`
- `product_stock_update`

これらを `audit_events` に保存する。`source_ip_hash` は IP をそのまま残さず HMAC hash として扱い、`user_agent_summary` は長さと制御文字を抑えた要約だけを保存する。

## 監視・監査 API

- `GET /api/auth/audit-events`
  - Bearer token の本人に紐づく監査イベントだけを返す
- `GET /api/security/audit-events`
  - `admin` / `support` 向けに監査イベントを返す
- `GET /api/security/monitoring/summary`
  - `audit_events` を集計し、NIDS/HIDS 相当の認証異常シグナルを返す

## DB 初期化の注意

新規DBは [postgres/init/001_products.sql](/home/buchi/WebProgramming-ActiveLearner/postgres/init/001_products.sql)、既存DBの統合アカウント移行は `postgres/init/003_unified_accounts.sql` で定義している。移行は `customer` / `seller` を `member` に変換するが、refresh token テーブルには触れない。

既存の `postgres_data` volume が残っている環境では、PostgreSQL の docker-entrypoint 初期化 SQL は自動再実行されない。その場合は管理者ユーザーで SQL を適用するか、学習環境であれば volume を再作成してから起動する。

既存DBへは次のように適用する。refresh token の行は更新・削除されない。

```bash
docker compose exec -T postgres psql -U postgres -d app_db < postgres/init/003_unified_accounts.sql
```

権限変換の単体テストは FastAPI 依存パッケージを導入した環境で実行する。

```bash
PYTHONPATH=fastapi python -m unittest discover -s fastapi/tests
```

例:

```bash
curl -k -i https://127.0.0.1/api/products
curl -k -i https://127.0.0.1/api/product?sku=sample-001
curl -k -i https://127.0.0.1/api/products \
  -H 'Authorization: Bearer <access token>' \
  -H 'Content-Type: application/json' \
  -d '{"sku":"sample-001","name":"Sample Product","price_cents":1200,"stock":10}'
curl -k -i https://127.0.0.1/api/product/stock \
  -H 'Authorization: Bearer <access token>' \
  -H 'Content-Type: application/json' \
  -d '{"sku":"sample-001","stock":8}'
curl -k -i https://127.0.0.1/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.test","password":"example-password-123"}'
curl -k -i https://127.0.0.1/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.test","password":"example-password-123"}'
curl -k -i https://127.0.0.1/api/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"<refresh token>"}'
curl -k -i https://127.0.0.1/api/auth/audit-events \
  -H 'Authorization: Bearer <access token>'
```

## 補足

- FastAPI は別コンテナとして動作する
- `internal-firewall` からだけ `api_net` 経由で到達する前提
- PostgreSQL は `db_net` 経由で参照する

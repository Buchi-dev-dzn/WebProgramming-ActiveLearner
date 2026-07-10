# Auth and Database Cryptography Design

このドキュメントは、今後 `FastAPI + PostgreSQL` にログイン機能と認証用データ管理を追加するための設計メモです。

目的は、CRYPTREC 暗号リストを参考にしながら、DB に保存する認証情報・個人情報をどのように保護するかを事前に整理することです。

## 参照する基準

- CRYPTREC 暗号リスト
  - https://www.cryptrec.go.jp/list/cryptrec-ls-0001-2022r2.pdf
  - 最終更新: 2026-03-30
- CRYPTREC リスト説明
  - https://www.cryptrec.go.jp/list.html
- OWASP Password Storage Cheat Sheet
  - https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html

CRYPTREC 暗号リストは、暗号アルゴリズム、鍵長、利用モードを選ぶための根拠として使います。

一方で、パスワード保存は単純な暗号化ではなく、パスワード保存専用の考え方が必要です。そのため、パスワードハッシュの反復回数や保存形式は OWASP の Password Storage Cheat Sheet も併用します。

## 基本方針

### パスワードは暗号化しない

ログイン用パスワードは、復号できる形で保存しません。

つまり、次のような保存は避けます。

```text
password = AES(password)
```

パスワードは本人確認に使えればよく、元の平文へ戻す必要はありません。そのため、復元不能な password hash として保存します。

採用候補:

```text
PBKDF2-HMAC-SHA-256
```

理由:

- `HMAC` と `SHA-256` は CRYPTREC 暗号リスト上で説明しやすい要素である
- PBKDF2 はパスワード保存用途で広く使われている
- OWASP では FIPS-140 などを意識する場合、PBKDF2-HMAC-SHA-256 を 600,000 iterations 以上で使う選択肢が示されている

保存形式案:

```text
pbkdf2_sha256$600000$salt_b64$hash_b64
```

保存するもの:

- algorithm
- iterations
- salt
- hash

保存しないもの:

- 平文 password
- 復号可能な password

### 個人情報は暗号化する

メールアドレス、氏名、住所、電話番号など、後で表示する必要がある情報は、復号可能な暗号化の対象にします。

採用候補:

```text
AES-256-GCM
```

理由:

- `AES` は CRYPTREC 暗号リストの 128 ビットブロック暗号に含まれる
- `GCM` は認証付き秘匿モードとして扱える
- 暗号化と改ざん検知を同時に扱える

保存形式案:

```text
email_ciphertext BYTEA
email_nonce BYTEA
email_key_id TEXT
```

nonce は暗号化ごとにランダム生成します。GCM では nonce の再利用を避ける必要があります。

### 検索が必要な個人情報は blind index を使う

メールアドレスを暗号化すると、そのままではログイン時に検索できません。

そのため、検索用には HMAC-SHA-256 による blind index を別に保存します。

例:

```text
email_lookup_hash = HMAC-SHA-256(EMAIL_LOOKUP_KEY, normalized_email)
```

保存するもの:

- 暗号化された email
- email 検索用 HMAC

保存しないもの:

- 平文 email

## 想定する DB テーブル

将来的に `users` テーブルを追加します。

```sql
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email_lookup_hash BYTEA NOT NULL UNIQUE,
    email_ciphertext BYTEA NOT NULL,
    email_nonce BYTEA NOT NULL,
    email_key_id TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_algorithm TEXT NOT NULL DEFAULT 'pbkdf2_hmac_sha256',
    password_iterations INTEGER NOT NULL DEFAULT 600000,
    role TEXT NOT NULL DEFAULT 'customer',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
```

インデックス案:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lookup_hash
ON users (email_lookup_hash);
```

## DB 権限方針

現在の構成では、PostgreSQL は独立した Docker コンテナとして `db_net` のみに所属しています。

この方針は維持します。

```text
Client
  -> external-firewall
  -> nips
  -> waf
  -> reverse-proxy
  -> internal-firewall
  -> fastapi-app
  -> postgres
```

DB に直接アクセスさせず、FastAPI 経由でのみ認証情報を扱います。

`app_user` に付与する権限は最小限にします。

```sql
GRANT SELECT, INSERT, UPDATE ON users TO app_user;
GRANT USAGE, SELECT ON SEQUENCE users_id_seq TO app_user;
```

`DELETE`, `DROP`, `CREATE`, `ALTER`, `SUPERUSER` は付与しません。

## FastAPI 側の想定 API

最初のログイン基盤として、次の API を想定します。

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
```

### POST /api/auth/register

入力:

```json
{
  "email": "user@example.com",
  "password": "example-password"
}
```

処理:

- email を正規化する
- email を AES-256-GCM で暗号化する
- email_lookup_hash を HMAC-SHA-256 で作る
- password を PBKDF2-HMAC-SHA-256 でハッシュ化する
- users に保存する

返却:

```json
{
  "id": 1,
  "email": "user@example.com",
  "role": "customer"
}
```

### POST /api/auth/login

入力:

```json
{
  "email": "user@example.com",
  "password": "example-password"
}
```

処理:

- email_lookup_hash で user を検索する
- password_hash を検証する
- 成功時に JWT を発行する
- last_login_at を更新する

返却:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 900
}
```

### GET /api/auth/me

入力:

```text
Authorization: Bearer <access_token>
```

処理:

- JWT を検証する
- `sub` から user を取得する
- email を復号して返す

返却:

```json
{
  "id": 1,
  "email": "user@example.com",
  "role": "customer",
  "is_active": true
}
```

## JWT 方針

初期段階では access token のみを扱います。

候補:

```text
HS256
```

JWT に含める claim:

- `sub`
  - user id
- `role`
  - customer / admin など
- `iat`
  - 発行時刻
- `exp`
  - 有効期限
- `iss`
  - 発行者

初期値:

```text
access token lifetime = 15 minutes
```

refresh token は次の段階で検討します。

## 必要な鍵と環境変数

FastAPI コンテナに次の環境変数を渡す想定です。

```text
JWT_SECRET_KEY_B64
DATA_ENCRYPTION_KEY_B64
EMAIL_LOOKUP_KEY_B64
```

用途:

- `JWT_SECRET_KEY_B64`
  - JWT 署名用
- `DATA_ENCRYPTION_KEY_B64`
  - AES-256-GCM による個人情報暗号化用
- `EMAIL_LOOKUP_KEY_B64`
  - email blind index 用 HMAC key

学習環境では Docker Compose の environment で渡してもよいですが、本番では secrets manager や HSM 相当の仕組みに移すべきです。

## ログ出力方針

ログに出してはいけないもの:

- 平文 password
- password_hash
- email_ciphertext
- email_lookup_hash
- JWT
- 暗号鍵

ログに出してよいもの:

- request_id
- user_id
- 成功 / 失敗の結果
- HTTP status
- 失敗理由の大分類

ログイン失敗時も、次のように理由を詳細に分けすぎないようにします。

```json
{
  "error": "invalid_credentials"
}
```

## WAF で許可する予定のルート

認証 API を追加する場合、WAF 側の許可ルートに次を追加します。

```text
POST:/api/auth/register
POST:/api/auth/login
GET:/api/auth/me
HEAD:/api/auth/me
```

それ以外の `/api/auth/...` は原則 `404` または `403` とします。

## 検証観点

### 正常系

- ユーザー登録できる
- 同じ email で重複登録できない
- 正しい password でログインできる
- JWT 付きで `/api/auth/me` にアクセスできる
- email は復号されてレスポンスに表示できる

### 異常系

- 誤 password は `401`
- 存在しない email は `401`
- 無効 JWT は `401`
- 期限切れ JWT は `401`
- token なしの `/api/auth/me` は `401`

### DB 保存確認

- password の平文が DB に存在しない
- email の平文が DB に存在しない
- `password_hash` は `pbkdf2_sha256$600000$...` 形式である
- `email_ciphertext` は暗号化済みである
- `email_lookup_hash` は HMAC であり、平文 email ではない

### 経路確認

- 外部公開入口から認証 API へ到達できる
- PostgreSQL へ外部から直接到達できない
- DB 操作は FastAPI 経由でのみ成立する

## 今後の拡張候補

- refresh token テーブル
- password reset token テーブル
- login attempt / account lockout
- MFA
- admin role
- key rotation
- 旧 password hash 方式からの段階的移行
- refresh token の失効管理
- DB バックアップ暗号化

## まとめ

この設計では、CRYPTREC 暗号リストを次のように使います。

- 個人情報暗号化
  - `AES-256-GCM`
- password hash の構成要素
  - `HMAC`
  - `SHA-256`
- 検索用 blind index
  - `HMAC-SHA-256`

ただし、パスワードは復号可能にする必要がないため、AES などで暗号化して保存するのではなく、PBKDF2-HMAC-SHA-256 による復元不能なハッシュとして保存します。

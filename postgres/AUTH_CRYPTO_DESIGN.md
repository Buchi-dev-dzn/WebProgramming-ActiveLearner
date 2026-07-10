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

## EC / Marketplace として保存する情報

最終的に Amazon 的な「購入者が商品を買える」「出品者が商品を登録できる」Web アプリのバックエンドにする場合、DB には認証情報だけでなく、出品、在庫、注文、配送、決済、レビューなどの情報を持つ必要があります。

ただし、すべてを同じ強度で暗号化するわけではありません。

基本分類は次です。

- 認証情報
  - 復元不能な hash として保存する
- 個人情報
  - 復号が必要なものは AES-256-GCM で暗号化する
- 検索が必要な個人情報
  - HMAC-SHA-256 の blind index を別に保存する
- 商品公開情報
  - 検索や一覧表示が必要なので通常カラムとして保存する
- 決済カード情報
  - 原則 DB に保存しない
  - 決済代行サービスの token / customer id のみ保存する
- 監査ログ
  - 改ざん検知を意識し、重要イベントを追跡できる形で保存する

### ユーザー / アカウント情報

購入者・出品者・管理者を同じ `users` に持たせ、役割は `role` や別テーブルで表します。

保存候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| user id | 通常保存 | 内部参照用 |
| email | AES-256-GCM + HMAC blind index | 表示とログイン検索が必要 |
| password | PBKDF2-HMAC-SHA-256 | 復号不要 |
| role | 通常保存 | 認可判定に使う |
| is_active | 通常保存 | アカウント停止判定 |
| created_at / updated_at | 通常保存 | 監査・運用 |
| last_login_at | 通常保存 | セキュリティ確認 |

将来の role 候補:

```text
customer
seller
admin
support
```

### 出品者プロフィール

出品機能を持つ場合、ユーザーとは別に `seller_profiles` を持つと整理しやすいです。

保存候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| seller id | 通常保存 | 内部参照用 |
| user id | 通常保存 | users との関連 |
| store name | 通常保存 | 公開表示される |
| store description | 通常保存 | 公開表示される |
| business email | AES-256-GCM + HMAC blind index | 連絡先として個人情報になり得る |
| phone number | AES-256-GCM + HMAC blind index | 個人情報 |
| business address | AES-256-GCM | 個人情報・事業者情報 |
| verification status | 通常保存 | 審査状態 |
| payout account token | token のみ保存 | 口座情報本体は保持しない |

事業者確認や本人確認書類を扱う場合、画像や書類そのものを DB に直接入れるのではなく、オブジェクトストレージに暗号化して置き、DB には参照 ID と状態だけを持たせます。

### 商品 / 出品情報

商品情報は検索・一覧・詳細表示に使うため、基本的には通常カラムとして保存します。

保存候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| product id | 通常保存 | 内部参照用 |
| seller id | 通常保存 | 出品者との関連 |
| sku | 通常保存 | 在庫管理・検索 |
| title | 通常保存 | 公開表示 |
| description | 通常保存 | 公開表示 |
| price_cents | 通常保存 | 決済・表示 |
| currency | 通常保存 | 決済・表示 |
| stock | 通常保存 | 在庫管理 |
| status | 通常保存 | draft / active / suspended |
| category id | 通常保存 | 検索・分類 |
| image urls | 通常保存 | 公開表示 |
| created_at / updated_at | 通常保存 | 管理 |

商品説明やタイトルは公開情報ですが、XSS や HTML injection の入口になり得るため、保存前または表示時にサニタイズ方針を決めます。

初期段階では、商品説明は plain text として保存し、HTML は許可しない方針が安全です。

### 商品画像 / 添付ファイル

画像ファイル本体は DB に保存せず、ファイルストレージに保存します。

DB に保存する候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| image id | 通常保存 | 内部参照 |
| product id | 通常保存 | 商品との関連 |
| object key | 通常保存 | ストレージ参照 |
| content type | 通常保存 | 配信制御 |
| file size | 通常保存 | 制限確認 |
| checksum | SHA-256 digest | 改ざん・重複確認 |
| scan status | 通常保存 | マルウェアスキャン状態 |

アップロード時は、拡張子だけでなく MIME type、サイズ、画像としての実体、マルウェアスキャン結果を見る必要があります。

### カート / 注文情報

カートは一時的な状態、注文は会計・配送・監査に使う確定情報として扱います。

保存候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| cart id | 通常保存 | 内部参照 |
| user id | 通常保存 | 所有者 |
| product id | 通常保存 | 商品参照 |
| quantity | 通常保存 | 数量 |
| order id | 通常保存 | 内部参照 |
| order status | 通常保存 | paid / shipped / canceled |
| item price snapshot | 通常保存 | 購入時価格の記録 |
| shipping address | AES-256-GCM | 個人情報 |
| recipient name | AES-256-GCM | 個人情報 |
| recipient phone | AES-256-GCM + HMAC blind index | 個人情報・検索可能性 |
| ordered_at | 通常保存 | 監査 |

注文では、商品名や価格を商品テーブルから毎回参照するだけでなく、購入時点の snapshot を保存するべきです。後から商品名や価格が変わっても、注文履歴の意味が壊れないようにするためです。

### 配送情報

配送情報は個人情報を多く含むため、暗号化対象です。

保存候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| shipment id | 通常保存 | 内部参照 |
| order id | 通常保存 | 注文との関連 |
| carrier | 通常保存 | 配送会社 |
| tracking number | AES-256-GCM + HMAC blind index | 悪用リスクがある |
| shipping address | AES-256-GCM | 個人情報 |
| shipment status | 通常保存 | shipped / delivered |
| shipped_at / delivered_at | 通常保存 | 追跡 |

tracking number は利用者やサポートが検索する可能性があるため、必要なら blind index を持ちます。

### 決済情報

カード番号、セキュリティコード、口座番号などは、このアプリの DB に直接保存しない方針にします。

保存してよい候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| payment id | 通常保存 | 内部参照 |
| order id | 通常保存 | 注文との関連 |
| provider | 通常保存 | Stripe など |
| provider payment id | 通常保存 | 決済代行側の参照 |
| provider customer id | 通常保存 | 決済代行側の顧客参照 |
| amount_cents | 通常保存 | 金額 |
| currency | 通常保存 | 通貨 |
| payment status | 通常保存 | succeeded / failed |
| card brand | 通常保存可 | 表示用の非機密情報 |
| card last4 | 通常保存可 | 表示用の非機密情報 |

保存しないもの:

- card number
- CVV / CVC
- magnetic stripe data
- raw payment credentials

決済情報は PCI DSS の対象になりやすいため、学習用途でも「カード情報本体は持たない」設計にしておくのが安全です。

### レビュー / 問い合わせ / メッセージ

レビューは公開情報ですが、問い合わせや出品者とのメッセージは個人情報が混ざる可能性があります。

保存候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| review text | 通常保存 | 公開表示される |
| rating | 通常保存 | 公開表示される |
| support message | AES-256-GCM | 個人情報が混ざり得る |
| buyer-seller message | AES-256-GCM | 非公開会話 |
| moderation status | 通常保存 | 管理 |

公開レビューでも、メールアドレスや電話番号などを投稿できないように入力検査・モデレーションを検討します。

### 監査ログ / セキュリティイベント

ログイン、出品、価格変更、在庫変更、注文状態変更などは監査ログに残します。

保存候補:

| 情報 | 保存方法 | 理由 |
| --- | --- | --- |
| event id | 通常保存 | 内部参照 |
| actor user id | 通常保存 | 操作者 |
| action | 通常保存 | login / product_update など |
| target type / target id | 通常保存 | 対象 |
| request id | 通常保存 | 通信ログとの関連 |
| source ip hash | HMAC-SHA-256 | IP を直接保存しない選択肢 |
| user agent summary | 通常保存 | 調査用 |
| created_at | 通常保存 | 監査 |

監査ログには平文 password、JWT、暗号鍵、カード情報、暗号化前の個人情報を出してはいけません。

## Marketplace 向けテーブル候補

初期実装で一気に全部作る必要はありませんが、将来の分割を考えると次のようなテーブル群になります。

```text
users
seller_profiles
products
product_images
carts
cart_items
orders
order_items
shipments
payments
reviews
support_messages
audit_events
```

最初に作る優先度は次です。

1. `users`
2. `seller_profiles`
3. `products`
4. `orders`
5. `order_items`
6. `payments`
7. `audit_events`

現在の `products` テーブルは学習用の最小実装です。将来的には、`seller_id`, `title`, `description`, `currency`, `status`, `category_id` などを持つ出品向けテーブルへ拡張します。

## データ保護レベルの整理

| 分類 | 例 | 保存方針 |
| --- | --- | --- |
| 認証秘密 | password | PBKDF2-HMAC-SHA-256 hash |
| 認証 token | JWT, reset token | DB には原則 hash / jti / 失効情報のみ |
| 個人情報 | email, phone, address, recipient name | AES-256-GCM |
| 個人情報検索キー | email search, phone search | HMAC-SHA-256 blind index |
| 公開商品情報 | title, description, image url | 通常保存 |
| 注文記録 | price snapshot, quantity, status | 通常保存 |
| 配送追跡番号 | tracking number | AES-256-GCM + 必要なら HMAC blind index |
| 決済カード情報 | card number, CVV | 保存しない |
| 決済参照 | provider payment id, last4 | 通常保存 |
| 監査情報 | action, actor id, target id | 通常保存 |

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

# PostgreSQL Database

このディレクトリには、独立した `postgres` コンテナで管理する DB 初期化 SQL を置きます。

## 役割

- `postgres` は FastAPI とは別コンテナとして動作する
- Docker ネットワーク上では `db_net` のみに所属する
- ホスト側には `5432` を公開しない
- `db_net` に接続するアプリケーションは `fastapi-app` のみ
- テーブル定義や制約は DB 側の SQL として管理する
- 初期化は PostgreSQL 管理ユーザーで行い、FastAPI は制限付きの `app_user` で接続する

## 初期化 SQL

- [init/001_products.sql](/home/buchi/WebProgramming-ActiveLearner/postgres/init/001_products.sql)
  - `products` テーブルを作成する
  - `sku` の一意制約、金額・在庫の CHECK 制約、SKU 形式制約を DB 側にも置く
  - `app_user` を `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE` で作成する
  - `app_user` には `products` への `SELECT`, `INSERT`, `UPDATE` と sequence 利用だけを許可する

PostgreSQL 公式イメージの仕様により、`/docker-entrypoint-initdb.d` の SQL は DB データディレクトリが空の初回起動時だけ実行されます。

既に `postgres_data` ボリュームが存在する環境でこの SQL を反映したい場合は、データを消してよければ次のように初期化し直します。

```bash
docker compose down -v
docker compose up -d --build
```

既存データを残す場合は、`postgres` コンテナ内で SQL を手動適用します。

```bash
docker compose exec -T postgres psql -U app_user -d app_db < postgres/init/001_products.sql
```

## アクセス境界

外部クライアントは DB に直接到達しません。通常経路は次です。

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

DB に対する SQL 実行は FastAPI のみが担当します。FastAPI 側では `asyncpg` の bind parameter を使い、ユーザー入力を SQL 文字列へ連結しません。

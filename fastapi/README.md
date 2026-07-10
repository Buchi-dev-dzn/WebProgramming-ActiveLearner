# FastAPI Application

このディレクトリには、内部 API サービスとして動く `fastapi-app` の実装を置きます。

## 役割

- `internal-firewall` の後段で業務 API を返す
- PostgreSQL への疎通状態を `/api/health` で返す
- PostgreSQL に対する商品データの作成・取得・更新を行う
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
  - 商品を作成する
- `GET /api/product?sku=...`
  - SKU 指定で商品を 1 件返す
- `POST /api/product/stock`
  - SKU 指定で在庫数を更新する

## SQL と DB アクセスの扱い

- PostgreSQL は `db_net` にだけ所属し、ホスト側には `ports` を公開しない
- `db_net` に接続しているのは `fastapi-app` と `postgres` のみ
- 外部からは `external-firewall -> nips -> waf -> reverse-proxy -> internal-firewall -> fastapi-app` を通る必要がある
- FastAPI では `asyncpg` の `$1`, `$2` 形式の bind parameter だけで SQL を実行する
- FastAPI は DB 管理ユーザーではなく、制限付きの `app_user` で PostgreSQL に接続する
- テーブル作成は [postgres/init/001_products.sql](/home/buchi/WebProgramming-ActiveLearner/postgres/init/001_products.sql) で DB コンテナ側に分離する
- SKU は `^[A-Za-z0-9._-]+$` に制限し、不要な文字を API 入力で受け付けない

例:

```bash
curl -k -i https://127.0.0.1/api/products
curl -k -i https://127.0.0.1/api/product?sku=sample-001
curl -k -i https://127.0.0.1/api/products \
  -H 'Content-Type: application/json' \
  -d '{"sku":"sample-001","name":"Sample Product","price_cents":1200,"stock":10}'
curl -k -i https://127.0.0.1/api/product/stock \
  -H 'Content-Type: application/json' \
  -d '{"sku":"sample-001","stock":8}'
```

## 補足

- FastAPI は別コンテナとして動作する
- `internal-firewall` からだけ `api_net` 経由で到達する前提
- PostgreSQL は `db_net` 経由で参照する

# FastAPI Test

> 実装状況: 2026-07-24 時点。`check_fastapi.py` は商品やユーザーを作成するため、使い捨ての学習環境で実行してください。

このテストは、Mac ホストなど VM 外部から `fastapi-app` の API 応答と劣化時の振る舞いを確認するためのものです。

## テスト対象

- `/api/health` が PostgreSQL 正常時に `200` / `status=ok` を返すか
- `/api/info` が FastAPI の構成情報を返すか
- `request_id` がヘッダ伝播結果として本文に含まれるか
- `/api/products` で PostgreSQL に商品を作成できるか
- `/api/product?sku=...` で作成済み商品を取得できるか
- `/api/product/stock` で在庫更新ができるか
- `/api/auth/register` で seller ユーザーを登録できるか
- `/api/auth/login` で JWT を取得できるか
- `/api/auth/me` で Bearer token からユーザー情報を取得できるか
- `/api/seller/profile` で出品者プロフィールを作成・取得できるか
- PostgreSQL 停止時に `/api/health` が `503` / `status=degraded` を返すか
  - 現行チェーンでは `503 {"error":"upstream_unavailable"}` に正規化される場合も許容する

## スクリプト

- [check_fastapi.py](/home/buchi/WebProgramming-ActiveLearner/other/test/fastapi/check_fastapi.py)

## 使い方

通常確認:

```bash
python3 other/test/fastapi/check_fastapi.py 192.168.64.4
```

JSON で出す:

```bash
python3 other/test/fastapi/check_fastapi.py 192.168.64.4 --json
```

PostgreSQL 停止比較:

```bash
docker compose stop postgres
python3 other/test/fastapi/check_fastapi.py 192.168.64.4 --expect-degraded-health
docker compose start postgres
```

## 期待値

通常時:

- `GET /api/health`
  - `200`
  - `status=ok`
  - `checks.postgres.status=ok`
- `GET /api/info`
  - `200`
  - `service=fastapi-api`
  - `dependencies=["postgres"]`
  - `request_id` を含む
- `POST /api/products`
  - `201`
  - 作成した `sku` を返す
- `GET /api/product?sku=...`
  - `200`
  - 作成した商品を返す
- `POST /api/product/stock`
  - `200`
  - 更新後の `stock` を返す
- `POST /api/auth/register`
  - `201`
  - 登録した `email` と `role=seller` を返す
- `POST /api/auth/login`
  - `200`
  - `access_token` を返す
- `GET /api/auth/me`
  - `200`
  - Bearer token に紐づくユーザーを返す
- `POST /api/seller/profile`
  - `200`
  - 出品者プロフィールを作成または更新する
- `GET /api/seller/profile`
  - `200`
  - 作成済み出品者プロフィールを返す

PostgreSQL 停止時:

- `GET /api/health`
  - `503`
  - `status=degraded` または `{"error":"upstream_unavailable"}`

## 比較ポイント

- 通常時
  - FastAPI は後段 API として正常な JSON を返す
  - SQL 操作は FastAPI 経由でだけ観測する
- PostgreSQL 停止時
  - FastAPI 自体は生きているが、依存障害は公開チェーン上で `503` として観測できる
- fastapi-app 停止時
  - `reverse-proxy` 側の `503 {"error":"upstream_unavailable"}` 比較に切り替わる

## 異常時の見方

- `/api/info` が `200` だが `service=fastapi-api` でない
  - 後段アプリケーションの取り違えを疑う
- PostgreSQL 停止時でも `/api/health` が `200`
  - ヘルスチェックや DB 接続の扱い漏れを疑う

## 検証結果メモ

2026-07-10 に `127.0.0.1` の公開入口経由で確認しました。

実行コマンド:

```bash
python3 other/test/fastapi/check_fastapi.py 127.0.0.1
```

確認できたこと:

- `GET /api/health` は `200` を返し、PostgreSQL check は `ok`
- `GET /api/info` は `service=fastapi-api` と DB 接続方針を返す
- `POST /api/products` は商品を PostgreSQL に作成できる
- `GET /api/product?sku=...` は作成した商品を取得できる
- `POST /api/product/stock` は在庫数を更新できる
- `GET /api/products` は作成済み商品一覧を返す
- 存在しない SKU は `404` を返す
- `X-Request-Id` は reverse proxy から FastAPI レスポンス本文まで伝播する

結果:

```text
health_shape_ok yes
info_shape_ok yes
request_id_propagated yes
product_shape_ok yes
auth_shape_ok yes
all_matched yes
```

補足:

- この検証は外部公開入口から `external-firewall -> nips -> waf -> reverse-proxy -> internal-firewall -> fastapi-app -> postgres` を通る経路で実施した
- Codex 実行環境からは Docker API への `docker compose ps` が permission denied になったため、コンテナ一覧ではなく HTTP/API 応答で稼働を確認した

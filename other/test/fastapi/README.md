# FastAPI Test

このテストは、Mac ホストなど VM 外部から `fastapi-app` の API 応答と劣化時の振る舞いを確認するためのものです。

## テスト対象

- `/api/health` が PostgreSQL 正常時に `200` / `status=ok` を返すか
- `/api/info` が FastAPI の構成情報を返すか
- `request_id` がヘッダ伝播結果として本文に含まれるか
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
PostgreSQL 停止時:

- `GET /api/health`
  - `503`
  - `status=degraded` または `{"error":"upstream_unavailable"}`

## 比較ポイント

- 通常時
  - FastAPI は後段 API として正常な JSON を返す
- PostgreSQL 停止時
  - FastAPI 自体は生きているが、依存障害は公開チェーン上で `503` として観測できる
- fastapi-app 停止時
  - `reverse-proxy` 側の `503 {"error":"upstream_unavailable"}` 比較に切り替わる

## 異常時の見方

- `/api/info` が `200` だが `service=fastapi-api` でない
  - 後段アプリケーションの取り違えを疑う
- PostgreSQL 停止時でも `/api/health` が `200`
  - ヘルスチェックや DB 接続の扱い漏れを疑う

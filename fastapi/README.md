# FastAPI Application

このディレクトリには、内部 API サービスとして動く `fastapi-app` の実装を置きます。

## 役割

- `internal-firewall` の後段で業務 API を返す
- PostgreSQL への疎通状態を `/api/health` で返す
- `reverse-proxy` から引き継がれた `X-Request-Id` と `X-Forwarded-*` を前提に動く

## 現在のエンドポイント

- `GET /health`
  - コンテナ内直通のヘルスチェック
- `GET /api/health`
  - 外部経路の集約ヘルスチェック
- `GET /api/info`
  - 現構成の説明用レスポンス

## 補足

- FastAPI は別コンテナとして動作する
- `internal-firewall` からだけ `api_net` 経由で到達する前提
- PostgreSQL は `db_net` 経由で参照する

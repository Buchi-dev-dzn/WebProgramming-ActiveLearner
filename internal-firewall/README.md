# Internal Firewall

このディレクトリには、`reverse-proxy` の後段に置く `internal-firewall` コンテナの設定をまとめます。

## 役割

- DMZ の `reverse-proxy` からだけ受ける内部境界を表現する
- `/api/` 系だけを後段の `fastapi-app` に転送する
- それ以外の一般リクエストは `403` で閉じる
- `X-Request-Id` と `X-Forwarded-*` を FastAPI へ引き継ぐ

## 現在の実装

- Nginx を internal firewall として使用
- `reverse-proxy` から `app_net` で到達する前提
- `fastapi-app:8000` を単一 upstream として使用
- `/api` は `/api/` へ正規化
- upstream 障害時は `502/504` を JSON の `503 {"error":"upstream_unavailable"}` に変換する
- 後段アプリケーション自身が返す `503` は、そのまま外へ返す
- upstream timeout は短めにして、後段停止時に外側比較しやすくしている

## 通信位置づけ

```text
external-firewall
  -> nips
  -> waf
  -> reverse-proxy
  -> internal-firewall
  -> fastapi-app
  -> postgres
```

この層の目的は、`reverse-proxy` が到達できる内部経路をさらに絞り、業務 API を持つ FastAPI を直接 DMZ に露出させないことです。

## ファイル

- `Dockerfile`
  - internal firewall 用 Nginx イメージ定義
- `nginx.conf`
  - ログ形式、`conf.d` 読み込み、共通設定
- `conf.d/default.conf`
  - `/health`, `/api/`, `/` の制御

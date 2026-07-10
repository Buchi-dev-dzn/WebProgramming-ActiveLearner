# Internal Firewall

このディレクトリには、`reverse-proxy` の後段に置く `internal-firewall` コンテナの設定をまとめます。

## 役割

- DMZ の `reverse-proxy` からだけ受ける内部境界を表現する
- `/api/` 系だけを後段の `fastapi-app` に転送する
- それ以外の一般リクエストは `403` で閉じる
- `X-Request-Id` と `X-Forwarded-*` を FastAPI へ引き継ぐ

## 現在の実装

- Nginx を internal firewall として使用
- `app_net` 上の `reverse-proxy` 固定IP `172.30.0.10` からの通信だけを許可する
- それ以外の送信元は Nginx の `allow` / `deny all` で拒否する
- 送信先は `api_net` 上の `fastapi-app` 固定IP `172.31.0.20:8000` を単一 upstream として固定する
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

## ホワイトリスト

この層では、送信元と送信先を次のようにホワイトリスト化しています。

```text
許可する送信元:
  reverse-proxy on app_net
  172.30.0.10

許可する送信先:
  fastapi-app:8000 on api_net
  172.31.0.20:8000

許可するHTTPパス:
  /api/
```

`internal-firewall` は `app_net` と `api_net` の両方に所属しますが、`reverse-proxy` は `api_net` に所属しません。したがって、`reverse-proxy` から `fastapi-app` へ直接到達する経路はなく、必ず `internal-firewall` を通ります。

また、`internal-firewall` 側では `allow 172.30.0.10; deny all;` を設定しているため、将来 `app_net` に別コンテナが追加されても、明示的に許可しない限りこの内部境界へ入れません。

## ファイル

- `Dockerfile`
  - internal firewall 用 Nginx イメージ定義
- `nginx.conf`
  - ログ形式、`conf.d` 読み込み、共通設定
- `conf.d/default.conf`
  - `/health`, `/api/`, `/` の制御

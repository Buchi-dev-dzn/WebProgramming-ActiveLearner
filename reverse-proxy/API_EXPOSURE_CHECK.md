# Reverse Proxy / API Exposure Check

このメモは、現在の構成で Reverse Proxy がどのように FastAPI へ中継しているか、また FastAPI の内部アクセス方法がクライアントへ直接露出していないかを確認するためのものです。

## 結論

現在の構成では、クライアントが使う入口はホストの `80/443` だけです。

FastAPI の内部宛先である `fastapi-app:8000` は Docker 内部ネットワーク上の名前であり、ホストには `ports` で公開されていません。したがって、クライアントは FastAPI へ直接アクセスするのではなく、必ず次の経路を通ります。

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

## クライアントから見えるAPIアクセス方法

クライアントが使うAPIは次の形です。

```text
https://<host>/api/...
```

例:

```bash
curl -k -i https://127.0.0.1/api/health
curl -k -i https://127.0.0.1/api/info
curl -k -i https://127.0.0.1/api/products
```

クライアントに `fastapi-app:8000`、`internal-firewall:80`、`postgres:5432` のような内部宛先を渡す設計ではありません。

## Compose上の公開範囲

`docker-compose.yml` では、ホストに公開しているのは `external-firewall` のみです。

```yaml
external-firewall:
  ports:
    - "80:80"
    - "443:443"
```

一方、`fastapi-app` は次のように `expose` のみです。

```yaml
fastapi-app:
  expose:
    - "8000"
```

`expose` は Docker ネットワーク内のサービス間通信向けであり、ホストへの公開ではありません。そのため、ホスト側から `127.0.0.1:8000` にアクセスする経路は作られていません。

PostgreSQL も同様に `expose: 5432` のみで、ホストへ直接公開していません。

## Reverse Proxy の役割

`reverse-proxy/conf.d/default.conf` では、`internal-firewall:80` を upstream として定義しています。

```nginx
upstream internal_firewall_upstream {
  server internal-firewall:80;
}
```

RP は `/api/` 配下だけを後段へ中継します。

```nginx
location /api/ {
  proxy_pass http://internal_firewall_upstream;
}
```

`/health` は RP 自身のヘルスチェックとして `reverse-proxy ok` を返します。`/api/` 以外の一般パスは FastAPI へ流さず、`404` を返します。

## Internal Firewall の役割

`internal-firewall/conf.d/default.conf` では、後段の FastAPI を `172.31.0.20:8000` として定義しています。このIPは `api_net` 上の `fastapi-app` に割り当てています。

```nginx
upstream fastapi_upstream {
  server 172.31.0.20:8000;
}
```

ここでも `/api/` 配下だけを FastAPI へ中継します。

```nginx
location /api/ {
  proxy_pass http://fastapi_upstream;
}
```

それ以外のパスは `403` を返すため、RPを通過したあとも内部境界で公開範囲を絞っています。

加えて、`app_net` 上の `reverse-proxy` には固定IP `172.30.0.10` を割り当て、`internal-firewall` 側ではこのIPだけを許可しています。

```nginx
allow 172.30.0.10;
deny all;
```

これにより、送信元は `reverse-proxy` のみに絞られます。将来 `app_net` に別コンテナが追加されても、明示的に許可しない限り `internal-firewall` へ入れません。

送信先も `fastapi-app:8000` の単一 upstream に固定しているため、Internal Firewall のホワイトリストは次の形になります。

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

## 実測確認結果

確認時点では、公開入口経由で次の応答を確認しました。

```text
GET http://127.0.0.1/health
=> 200
=> reverse-proxy ok
```

これは RP 自身が応答していることを示します。

```text
GET https://127.0.0.1/api/info
=> 200
=> via: ["reverse-proxy", "internal-firewall", "fastapi-api"]
```

これは公開入口から FastAPI まで到達し、FastAPI が RP / internal-firewall 経由で到達した前提のレスポンスを返していることを示します。

一方で、FastAPI の直通ポートは接続できませんでした。

```text
GET https://127.0.0.1:8000/api/info
=> connection failed
```

この結果から、FastAPI の `8000` はホストに直接公開されていないと判断できます。

## FastAPI側のAPI

現在の FastAPI は `/api/...` に業務APIを集約しています。

- `GET /api/health`
- `GET /api/info`
- `GET /api/products`
- `POST /api/products`
- `GET /api/product?sku=...`
- `POST /api/product/stock`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/seller/profile`
- `GET /api/seller/profile`

DBアクセスは `asyncpg` の bind parameter を使っており、商品APIなどでSQL文字列に入力値を直接連結する実装にはなっていません。

## 注意点

現在の `/api/info` は、学習・検証用として内部構成を説明するレスポンスを返しています。

レスポンス内には `via`、`networks`、`database_access` などの説明情報が含まれます。これは認証情報や直通接続先ではありませんが、本番では内部構成の露出になり得ます。

本番化する場合は、次のいずれかにするのが自然です。

- `/api/info` を削除する
- 管理者向け認証を必須にする
- 返す情報を `service` と `status` 程度に絞る

また、Compose の `environment` には開発用の `JWT_SECRET_KEY_B64`、`DATA_ENCRYPTION_KEY_B64`、`EMAIL_LOOKUP_KEY_B64`、`DATABASE_URL` が書かれています。これらはクライアントへ返されるものではありませんが、リポジトリを共有・公開すると漏れるため、本番では secrets manager や非コミットの `.env` へ移すべきです。

## まとめ

現在のRPは、`/api/` を `internal-firewall` へ中継する役割として動作しています。

クライアントは `https://<host>/api/...` だけを使い、FastAPI の内部宛先 `fastapi-app:8000` には直接到達しません。さらに `internal-firewall` でも `/api/` だけを FastAPI へ通すため、RPの後段でも公開範囲を絞る構成になっています。

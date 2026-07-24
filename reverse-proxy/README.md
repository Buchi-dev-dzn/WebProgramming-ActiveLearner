# Reverse Proxy

> 実装状況: 2026-07-24 時点。DMZ 中継と request ID 付与を担当し、WAF 検査や認証は担当しません。

このディレクトリには、DMZ に置く `reverse-proxy` コンテナの設定と説明をまとめます。

## 役割

- 外部クライアントが直接触れられる公開領域の後段で HTTP/HTTPS を受ける
- `WAF` を通過したリクエストだけを内部の `internal-firewall` に中継する
- 内部アプリケーションを直接公開せず、DMZ の公開境界として振る舞う
- FastAPI など後段アプリケーションのレスポンスを代理返却する

今回の `reverse-proxy` は、防御ルールを細かく持つ層ではなく、`DMZ に置かれた公開中継点` として位置づけています。  
つまり、外部クライアントは `internal-firewall` や `fastapi-app` に直接到達せず、必ず `external-firewall`, `nips`, `waf` を通過したうえで、この `reverse-proxy` に到達します。

## 現在の実装

- Nginx を reverse proxy として使用
- `/api/` を `internal-firewall:80` へ転送
- `/health` を RP 自身のヘルスチェックとして返す
- `80/443` を listen するが、host へは直接公開しない
- 証明書は現状 `waf/certs/` を共有して使用
- `upstream` は現状 `internal-firewall:80` の単一系
- `/api/` 以外の一般リクエストは後段へ流さず `404` を返す
- `request_id` を付与し、後段にも引き継ぐ
- upstream 障害時は Nginx 既定の HTML ではなく JSON エラーを返す

## 現在の通信経路

現状の本線は次の通りです。

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

このうち `reverse-proxy` が担うのは、`waf` から渡された正規リクエストを内部の `internal-firewall` へ渡し、その応答をクライアントへ代理返却する部分です。

リクエスト / レスポンスの流れを RP 観点だけで書くと次です。

```text
1. client が HTTP/HTTPS リクエストを送る
2. 前段の external-firewall, nips, waf を通る
3. reverse-proxy がリクエストを受ける
4. /api/ 配下を internal-firewall:80 へ転送する
5. internal-firewall の後段 FastAPI がレスポンスを返す
6. reverse-proxy がそのレスポンスをクライアントへ返す
```

ここで重要なのは、`reverse-proxy` 自身が業務処理を実行するのではなく、後段アプリケーションの応答を代理で返すことです。

## Reverse Proxy として何をしているか

現在の設定では、`reverse-proxy` は次を行っています。

- `listen 80` と `listen 443 ssl` で HTTP/HTTPS を受ける
- `location /api/` を `internal-firewall:80` へ `proxy_pass` する
- `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-Port` を後段へ渡す
- `X-Request-Id` を付与して、クライアント応答と後段ログをひも付けやすくする
- `proxy_connect_timeout`, `proxy_send_timeout`, `proxy_read_timeout` を設定して、後段異常時に無制限待機しない
- upstream 障害時は `502/503/504` を JSON に変換して返す
- access log に upstream 宛先、upstream status、応答時間を残す
- `client_max_body_size` を 1MB に制限する
- `/health` で RP 自身の稼働確認を返す
- `/api/` 以外の一般公開パスは `404` にして、DMZ 上の公開面を広げすぎない

これにより、後段の `internal-firewall` や `fastapi-app` を外部へ直接さらさずに、DMZ 上の 1 点から内部へ中継する構成になっています。

## 今回ここで固定する RP の最低限仕様

現段階では、`reverse-proxy` の最低限仕様を次のように置きます。

- 公開対象パスは `/health` と `/api/` 系のみ
- `/api/` は後段 `internal-firewall` にそのまま中継する
- FastAPI のレスポンスは RP が代理返却する
- RP 自身は業務レスポンスを持たない
- 後段に渡す forwarded header は RP 側で明示する
- リクエスト追跡用の ID を RP で付与する
- 後段異常時に備えて timeout を持つ
- 後段異常時も RP らしい応答形式で返す
- DMZ の公開面を広げないため、不要パスは `404` で返す

この仕様により、RP を `通すための層` として保ちつつ、後段の FastAPI に移行しても前段の公開経路を変えずに済むようにします。

## Reverse Proxy としてまだ持たせていないもの

現時点では、次はまだ本格実装していません。

- upstream の明示定義
- 複数 FastAPI backend への負荷分散
- retry や failover 制御
- path ごとの厳密な公開制御
- 認証認可や業務ロジック
- WAF 相当の詳細検査
- 複数 upstream を前提にした冗長化

これは意図的です。  
現在は `WAF` を優先し、`reverse-proxy` には `DMZ の公開中継点` としての最小責務を持たせています。

## 他層との役割分担

- `waf`
  - 危険なリクエストを検査して遮断する
  - 現状では TLS 終端や詳細検査も前段で担っている
- `reverse-proxy`
  - 許可された通信を内部へ正しく流す
  - 後段の応答を代理返却する
- `internal-firewall`
  - 内部境界として `/api/` だけを FastAPI に流す
- `fastapi-app`
  - 実際の API 応答を返す

この分担により、`waf` は止める層、`reverse-proxy` は通す層、`internal-firewall` は絞る層、`fastapi-app` は処理する層として整理できます。

## 現状の設計意図

この `reverse-proxy` は、DMZ の考え方を Docker 上で再現するために置いています。

設計意図は次です。

- 公開境界と業務処理層を分ける
- `internal-firewall` と `fastapi-app` を host や外部クライアントから直接見せない
- 内部転送の起点を 1 か所に集約する
- 将来的に FastAPI を後段へ置いたときも、公開経路を変えずに差し替えられるようにする

特に今回の前提では、後段アプリケーションは `FastAPI` で再設計する想定です。  
そのため `reverse-proxy` は、FastAPI の前段にある DMZ の公開入口として整理しておくのが自然です。

## 現状の限界と今後の整理ポイント

現状の構成には、まだ整理途中の点もあります。

- `waf` 側でも TLS を扱っており、`reverse-proxy` 側の `443 ssl` は現時点では主経路で強くは使われていない
- `internal-firewall` と `fastapi-app` は分離したが、将来的な API Gateway 分離までは未実装である
- エラーレスポンスは JSON 化したが、詳細な障害分類まではまだ行っていない
- ログは取り始めたが、集約や可視化はまだ未整備

## 今回追加した運用寄りの整備

今回の段階で、RP を単なる疎通確認用から少し進めて、運用上の観測性も持たせました。

- `request_id` をレスポンスと後段転送の両方に付与
- access log に upstream 宛先、upstream status、処理時間を追加
- `/api` へのアクセスを `/api/` に正規化
- upstream 障害時に JSON エラーを返す
- 単一 upstream 前提のため `proxy_next_upstream off` を明示

これにより、後段の FastAPI 化を進めたときも、`どのリクエストがどこへ流れ、どこで失敗したか` を RP 視点で追いやすくしています。

今後 `FastAPI` を本格化する際は、次を詰める必要があります。

- `reverse-proxy` が公開するパスをどこまでに限定するか
- `internal-firewall` と `fastapi-app` の間に追加の API Gateway を置くか
- timeout, error page, access log をどこまで RP 側で持つか
- forwarded header を FastAPI 側でどう解釈させるか

## ファイル

- `nginx.conf`
  - Nginx 全体設定
  - ログ形式、`conf.d` 読み込み
- `conf.d/default.conf`
  - DMZ 上の default server 設定
  - `/health`, `/api/`, `/` のルーティング

## 設計メモ

- 現時点では `WAF` を優先し、前段防御は `waf/` が担う
- `reverse-proxy` は防御装置ではなく、DMZ の公開中継点として責務を絞る
- 後段アプリケーションは `FastAPI` 化を前提に設計していく
- 将来的には upstream 定義、timeout、header 制御、公開パス制限を強化する

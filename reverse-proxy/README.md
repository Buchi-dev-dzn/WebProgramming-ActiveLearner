# Reverse Proxy

このディレクトリには、DMZ に置く `reverse-proxy` コンテナの設定と説明をまとめます。

## 役割

- 外部クライアントが直接触れられる公開領域の後段で HTTP/HTTPS を受ける
- `WAF` を通過したリクエストだけを内部の `application` に中継する
- 内部アプリケーションを直接公開せず、DMZ の公開境界として振る舞う
- FastAPI など後段アプリケーションのレスポンスを代理返却する

今回の `reverse-proxy` は、防御ルールを細かく持つ層ではなく、`DMZ に置かれた公開中継点` として位置づけています。  
つまり、外部クライアントは backend や application に直接到達せず、必ず `external-firewall`, `nips`, `waf` を通過したうえで、この `reverse-proxy` に到達します。

## 現在の実装

- Nginx を reverse proxy として使用
- `/api/` を `application:80` へ転送
- `/health` を RP 自身のヘルスチェックとして返す
- `80/443` を listen するが、host へは直接公開しない
- 証明書は現状 `waf/certs/` を共有して使用

## 現在の通信経路

現状の本線は次の通りです。

```text
Client
  -> external-firewall
  -> nips
  -> waf
  -> reverse-proxy
  -> application
  -> backend
  -> postgres
```

このうち `reverse-proxy` が担うのは、`waf` から渡された正規リクエストを内部の `application` へ渡し、その応答をクライアントへ代理返却する部分です。

リクエスト / レスポンスの流れを RP 観点だけで書くと次です。

```text
1. client が HTTP/HTTPS リクエストを送る
2. 前段の external-firewall, nips, waf を通る
3. reverse-proxy がリクエストを受ける
4. /api/ 配下を application:80 へ転送する
5. application の後段 backend がレスポンスを返す
6. reverse-proxy がそのレスポンスをクライアントへ返す
```

ここで重要なのは、`reverse-proxy` 自身が業務処理を実行するのではなく、後段アプリケーションの応答を代理で返すことです。

## Reverse Proxy として何をしているか

現在の設定では、`reverse-proxy` は次を行っています。

- `listen 80` と `listen 443 ssl` で HTTP/HTTPS を受ける
- `location /api/` を `application:80` へ `proxy_pass` する
- `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` を後段へ渡す
- `/health` で RP 自身の稼働確認を返す
- `/` では固定レスポンスを返し、RP 自体の生存を確認できるようにする

これにより、後段の application や backend を外部へ直接さらさずに、DMZ 上の 1 点から内部へ中継する構成になっています。

## Reverse Proxy としてまだ持たせていないもの

現時点では、次はまだ本格実装していません。

- upstream の明示定義
- 複数 backend への負荷分散
- retry や failover 制御
- timeout の詳細調整
- path ごとの厳密な公開制御
- 認証認可や業務ロジック
- WAF 相当の詳細検査

これは意図的です。  
現在は `WAF` を優先し、`reverse-proxy` には `DMZ の公開中継点` としての最小責務を持たせています。

## 他層との役割分担

- `waf`
  - 危険なリクエストを検査して遮断する
  - 現状では TLS 終端や詳細検査も前段で担っている
- `reverse-proxy`
  - 許可された通信を内部へ正しく流す
  - 後段の応答を代理返却する
- `application`
  - 内部アプリ層としてさらに `/api/` だけを backend へ流す
- `backend`
  - 実際の API 応答を返す

この分担により、`waf` は止める層、`reverse-proxy` は通す層、`backend` は処理する層として整理できます。

## 現状の設計意図

この `reverse-proxy` は、DMZ の考え方を Docker 上で再現するために置いています。

設計意図は次です。

- 公開境界と業務処理層を分ける
- backend を host や外部クライアントから直接見せない
- 内部転送の起点を 1 か所に集約する
- 将来的に FastAPI を後段へ置いたときも、公開経路を変えずに差し替えられるようにする

特に今回の前提では、後段アプリケーションは `FastAPI` で再設計する想定です。  
そのため `reverse-proxy` は、FastAPI の前段にある DMZ の公開入口として整理しておくのが自然です。

## 現状の限界と今後の整理ポイント

現状の構成には、まだ整理途中の点もあります。

- `waf` 側でも TLS を扱っており、`reverse-proxy` 側の `443 ssl` は現時点では主経路で強くは使われていない
- `application` コンテナ内にも nginx があり、RP と Internal Firewall の責務が 2 段になっている
- `/` の固定レスポンスは疎通確認用であり、本番用の公開仕様ではない

今後 `FastAPI` を本格化する際は、次を詰める必要があります。

- `reverse-proxy` が公開するパスをどこまでに限定するか
- `application` 内 nginx を残すか、FastAPI 直結に寄せるか
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

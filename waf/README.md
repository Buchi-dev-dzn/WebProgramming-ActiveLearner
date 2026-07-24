# WAF Layer

> 実装状況: 2026-07-24 時点。許可ルートと検査ルールの正本は `conf.d/default.conf`、比較用バイパスは `conf.d-pass-through/default.conf` です。後半の実測値は過去の検証記録です。

このディレクトリには、`nips` の後段に置く `waf` コンテナの設定を置いています。

## 役割

- `nips` から渡された HTTP/HTTPS を受ける
- Web アプリケーション向けのリクエストを詳細に検査する
- 正常な通信だけを `reverse-proxy` に渡す

## 現在の内容

- `nginx.conf`
  - WAF レイヤー全体の nginx 設定
- `conf.d/default.conf`
  - 簡易ルール
  - 許可 HTTP メソッド制限
  - 危険な User-Agent の拒否
  - 明らかな path traversal や SQLi/XSS を狙う文字列の拒否
- `certs/dev.crt`, `certs/dev.key`
  - HTTPS疎通確認用の自己署名証明書
  - SAN: `localhost`, `127.0.0.1`, `192.168.64.4`

## Step 1 での HTTPS の扱い

- 外部公開された`443`を`nips`経由でWAFが処理する
- TLS 終端は WAF で行う
- 証明書はローカル検証用の自己署名であり、本番用途ではない
- `curl -k https://<host>/api/health` のように疎通確認する前提

自己署名証明書は公開CAから信頼されないため、ブラウザでは警告が表示されます。警告なしで検証する場合は、開発端末の信頼ストアへ`dev.crt`を明示的に登録します。秘密鍵`dev.key`は登録・配布せず、本番環境では公的CAまたは組織内CAが発行した別の証明書へ置き換えます。

### 開発用証明書の内容

現在の証明書は次の接続先をSAN（Subject Alternative Name）に含みます。

```text
DNS:localhost
IP:127.0.0.1
IP:192.168.64.4
```

証明書の用途はTLS Web Server Authentication、有効期限は2028年10月26日です。秘密鍵は`600`、公開証明書は`644`のファイル権限にします。

内容と接続先の一致は次のコマンドで確認できます。

```bash
openssl x509 -in waf/certs/dev.crt \
  -noout -subject -issuer -dates -ext subjectAltName
openssl x509 -in waf/certs/dev.crt -noout -checkhost localhost
openssl x509 -in waf/certs/dev.crt -noout -checkip 127.0.0.1
openssl x509 -in waf/certs/dev.crt -noout -checkip 192.168.64.4
```

証明書を更新した場合は、WAFと外部入口を再作成して反映します。

```bash
sudo docker compose up -d --force-recreate waf external-firewall
curl -k -i https://192.168.64.4/api/health
```

`curl -k`は証明書検証を無効にします。開発時の疎通確認だけに使用し、通常の利用では開発端末に`dev.crt`を信頼登録します。

### 本番移行時

- `dev.crt`と`dev.key`を本番へコピーしない
- 公的CAまたは組織内CAから、本番ホスト名をSANに持つ証明書を取得する
- 本番用秘密鍵をリポジトリへコミットしない
- `waf/conf.d/default.conf`の証明書パスを本番用ファイルへ変更する
- 切り替え後に証明書チェーン、ホスト名、有効期限、TLS 1.2／1.3での接続を確認する

## 今回の構成での位置づけ

今回の本線は次の通りです。

```text
Client
  -> External Firewall
  -> NIPS
  -> WAF
  -> Reverse Proxy
  -> Internal Firewall
  -> Backend
```

役割分担は次のように整理します。

- `External Firewall`
  - 到達ポートを限定し、公開入口を 1 か所に集約する
- `NIPS`
  - 本線上で rate 異常や TLS handshake 異常を見て広く止める
- `WAF`
  - HTTP/HTTPS を詳細に見て、Web 向け攻撃を止める
  - 到達可能なルートを必要最小限に絞る

## 今回の変更内容

今回の厳格化では、単純な `危険文字列を含むか` だけではなく、`どのルートに到達させるか`, `どのヘッダを信用しないか`, `どの程度のリクエスト量を許すか` まで WAF 側で明示するようにした。

### 1. 許可ルート方式に変更した

従来は `location /` で広く後段へ流していたが、現在は次だけを許可している。

- `GET /`
- `HEAD /`
- `GET /health`
- `HEAD /health`
- `GET /api/health`
- `HEAD /api/health`
- `GET /api/info`
- `HEAD /api/info`
- `GET /api/products`
- `HEAD /api/products`
- `POST /api/products`
- `GET /api/product`
- `HEAD /api/product`
- `POST /api/product/stock`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `HEAD /api/auth/me`
- `GET /api/auth/audit-events`
- `HEAD /api/auth/audit-events`
- `GET /api/seller/profile`
- `HEAD /api/seller/profile`
- `POST /api/seller/profile`
- `GET /api/security/audit-events`
- `HEAD /api/security/audit-events`
- `GET /api/security/monitoring/summary`
- `HEAD /api/security/monitoring/summary`
- `POST /api/health`
- `POST /api/info`

これ以外は WAF 段階で `404` を返す。

意味:

- 不要な管理パス探索や未知エンドポイント探索を後段に流さない
- `reverse-proxy` や `internal-firewall` に届く前に到達面を絞れる
- 「通す URL を列挙する」形なので、後から監査しやすい

今回追加した認証・出品者プロフィール系ルートの意味:

- `/api/auth/register`
  - ユーザー登録の入口
  - email は後段 FastAPI で暗号化され、password は password hash として保存される
- `/api/auth/login`
  - ログインの入口
  - WAF はルート到達だけを許可し、認証判定は FastAPI が行う
- `/api/auth/refresh`
  - refresh token ローテーションの入口
  - token の HMAC hash 照合、失効判定、再発行は FastAPI が行う
- `/api/auth/logout`
  - refresh token 失効の入口
  - Bearer token と refresh token の対応確認は FastAPI が行う
- `/api/auth/me`
  - Bearer token 確認用
  - token の妥当性や権限判定は FastAPI が行う
- `/api/auth/audit-events`
  - ログイン中ユーザー本人の監査イベント確認用
  - 本人確認と返却範囲の制御は FastAPI が行う
- `/api/seller/profile`
  - 出品者プロフィールの作成・取得用
  - 事業者メール、電話番号、住所などは後段 FastAPI で暗号化される
- `/api/security/audit-events`
  - admin / support 向け監査イベント確認用
  - 権限判定は FastAPI が行う
- `/api/security/monitoring/summary`
  - admin / support 向け NIDS/HIDS 相当シグナル集計用
  - 集計対象は PostgreSQL の `audit_events`

WAF は暗号化、password hash、refresh token 検証、権限判定そのものは担当しない。WAF の役割は、認証 API、監査 API、出品者プロフィール API への到達面を必要な HTTP method と path に限定すること。

### 2. path ベースの遮断を強化した

従来の簡単な traversal 検知に加えて、次のような探索系 path を拒否するようにした。

- `/.git`
- `/.env`
- `/server-status`
- `/actuator`
- `/admin`
- `/console`
- `/boaform`
- `/wp-admin`
- `/wp-login.php`
- `/phpmyadmin`
- `/etc/passwd`
- `../` の通常表現
- `%2e%2e`, `%252e%252e%252f` などの URL エンコードや二重エンコード
- `%5c` を使うバックスラッシュ系 traversal

意味:

- 秘密情報や設定情報の露出を狙う典型パスを前段で止める
- エンコードを使った単純な回避をされにくくする
- 管理画面探索や既知ミドルウェア探索を早い段階で落とせる

### 3. query ベースの遮断を強化した

従来の `union select` や `<script>` に加えて、次のようなパターンも見ている。

- `select ... from`
- `sleep(`
- `benchmark(`
- `<svg`
- `javascript:`
- `onerror=`
- `onload=`
- `or 1=1`
- `information_schema`
- `load_file(`
- `into outfile`
- `${jndi:`
- query 内 traversal

意味:

- SQLi と XSS の初歩的な派生形を少し広く拾える
- JNDI 文字列のような分かりやすい危険シグネチャも前段で落とせる
- 「危険 query は通さない」という説明を具体化できる

### 4. URL override header を拒否するようにした

次のヘッダを WAF で拒否する。

- `X-Original-URL`
- `X-Rewrite-URL`

意味:

- 後段がこれらのヘッダを解釈する構成に変わった場合でも、前段で余計な URL 差し替えを防ぎやすい
- ルート制限をヘッダで迂回しようとする試行を抑止できる

### 5. request body サイズを縮小した

- 変更前
  - `client_max_body_size 1m`
- 変更後
  - `client_max_body_size 256k`

意味:

- この学習用 API で必要のない大きな body を早めに拒否できる
- 無意味に大きな POST を後段へ流さずに済む

### 6. WAF 段階のレート制限を追加した

`nginx.conf` で `limit_req_zone` を定義し、許可ルート側で `limit_req` を適用した。

- レート
  - `20r/s`
- burst
  - `20`
- 対象
  - `/`
  - `/api/health`
  - `/api/info`
  - `/api/products`
  - `/api/product`
  - `/api/product/stock`
  - `/api/auth/register`
  - `/api/auth/login`
  - `/api/auth/refresh`
  - `/api/auth/logout`
  - `/api/auth/me`
  - `/api/auth/audit-events`
  - `/api/seller/profile`
  - `/api/security/audit-events`
  - `/api/security/monitoring/summary`

意味:

- `NIPS` の広域レート制御とは別に、WAF で Web リクエスト量も抑えられる
- 正常系の疎通確認は通しつつ、極端な連打はここでも絞れる
- HTTP レイヤーでもう 1 段制御が入る

### 7. nginx の受理ポリシーも少し厳しくした

- `ignore_invalid_headers on`
- `underscores_in_headers off`
- `server_tokens off`

意味:

- 妥当でないヘッダを受け入れにくくする
- 不要なヘッダ解釈を減らす
- サーバ情報の露出を抑える

## 今回の WAF が見るもの

- 非許可 HTTP メソッド
- 危険な User-Agent
- path traversal や管理画面探索
- SQLi / XSS を疑う query
- URL override を狙う不審ヘッダ
- 未許可ルートへの探索
- 過大な request body

## 認証・出品 API 追加時の WAF の意味

今回の FastAPI 側の拡張により、ログイン・JWT・refresh token・監査イベント・出品者プロフィールの API が増えました。

WAF 側で追加したのは、これらの API を無条件に広く開けることではなく、次の条件だけを通すことです。

```text
POST /api/auth/register
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout
GET  /api/auth/me
GET  /api/auth/audit-events
POST /api/seller/profile
GET  /api/seller/profile
GET  /api/security/audit-events
GET  /api/security/monitoring/summary
```

これにより、例えば次のような未知ルートは後段へ渡しません。

```text
/api/auth/admin
/api/auth/debug
/api/auth/token-dump
/api/seller/delete
/api/seller/internal
/api/security/debug
```

認証情報や個人情報の保護は FastAPI と PostgreSQL の責務ですが、WAF はその前段で「そもそも想定していない URL を通さない」役割を持ちます。

## 実測で確認したいこと

- 正常な `/health` と `/api/health` は通る
- `sqlmap` などの明らかな危険 UA は `403`
- `?q=<script>` や `?q=union%20select` は `403`
- `/.git/config` や traversal 系 path は `403`
- `X-Original-URL` などの URL override header は `403`
- 未許可ルートは `404`
- 非許可メソッドは `405`

## 実測した検証結果

今回の本線で次を確認しました。

### 正常系

- `https://127.0.0.1/api/health`
  - `200 OK`
- `https://127.0.0.1/`
  - `200 OK`
  - body は `{"error":"not_found"}`

意味:

- `External Firewall -> NIPS -> WAF -> Reverse Proxy -> Application -> Backend` の本線が成立している
- WAF を挟んでも正常トラフィックは FastAPI まで通る

### 遮断系

- `curl -k -A "sqlmap" https://127.0.0.1/`
  - `403`
- `curl -k "https://127.0.0.1/?q=<script>"`
  - `403`
- `curl -k "https://127.0.0.1/?q=union%20select"`
  - `403`
- `curl -k -X PUT https://127.0.0.1/`
  - `405`

意味:

- 危険な User-Agent を WAF で止められた
- XSS / SQLi を疑う query を WAF で止められた
- 非許可メソッドを WAF で止められた

### 今回の役割分担

- `NIPS`
  - 広く止める層
  - rate 異常や TLS handshake 異常を見る
- `WAF`
  - Web に深く効く層
  - HTTP/HTTPS の詳細検査を行う

## 有効化・無効化と比較方法

### 有効化

`WAF` を有効にした状態で本線を起動する:

```bash
docker compose up -d waf nips external-firewall reverse-proxy
```

設定変更を反映して再作成する:

```bash
docker compose up -d --force-recreate waf nips external-firewall
```

状態確認:

```bash
docker compose ps waf nips external-firewall reverse-proxy
docker compose logs --tail=50 waf
```

### 無効化

`WAF` だけを止める:

```bash
docker compose stop waf
```

再開:

```bash
docker compose start waf
```

### 比較の考え方

比較したいのは次の 2 点です。

- 正常な Web リクエストは通るか
- Web 向け攻撃パターンを WAF が詳細に止めるか

### WAF 有効時の確認コマンド

```bash
curl -k -i https://127.0.0.1/
curl -k -i https://127.0.0.1/api/health
curl -k -i -A "sqlmap" https://127.0.0.1/
curl -k -i "https://127.0.0.1/?q=<script>"
curl -k -i "https://127.0.0.1/?q=union%20select"
curl -k -i -X PUT https://127.0.0.1/
```

期待値:

- `/`
  - `200`
- `/api/health`
  - `200`
- 危険 UA
  - `403`
- XSS / SQLi 風 query
  - `403`
- 非許可メソッド
  - `405`

### WAF 無効時の見え方

`waf` を止めると、本線は `external-firewall -> nips -> waf -> reverse-proxy` の途中で切れます。

そのため、今回の構成では次を確認できます。

- 正常な Web リクエストも FastAPI まで到達しなくなる
- `WAF` が単なる後付けルールではなく、本線上の Web 向け検査・中継要素であることが分かる

### 検査だけを無効化して比較する方法

`stop` だと `WAF` 自体が経路から消えるため、比較用途としては「通信断」と「防御無効化」が混ざります。  
そのため、このリポジトリでは pass-through 設定も用意しています。

pass-through で再起動する:

```bash
docker compose -f docker-compose.yml -f docker-compose.waf-bypass.yml up -d --force-recreate waf
```

通常の WAF に戻す:

```bash
docker compose up -d --force-recreate waf
```

pass-through 時の意味:

- `WAF` コンテナ自体は本線上に残る
- ただし危険 UA、危険 query、メソッド制限による遮断を行わない
- そのため Web リクエストは `reverse-proxy` 側へそのまま流れる

### 何が比較できるか

- `WAF` 有効時
  - 正常な Web リクエストは通る
  - 危険 UA、危険 query、非許可メソッドは `403/405`
- `WAF` 無効時
  - NIPS までは通っても、WAF 以降へ流れず正常通信も成立しない

pass-through 比較では次も確認できます。

- `WAF` pass-through 時
  - 正常な Web リクエストは通る
  - 通常 WAF が止める危険 UA や危険 query も、WAF 自体では止めなくなる
  - 「Web 向けの詳細防御は WAF が担っている」ことを説明しやすい

この比較により、`NIPS` が広域的な侵入防止を担当し、`WAF` が Web アプリケーション向けの詳細防御を担当する、という責務差を説明できます。

## 報告書向けのまとめ

今回の WAF は、`NIPS` の後段で HTTP/HTTPS を詳細に検査する層として実装した。  
実際に、正常な FastAPI 向けリクエストは `200 OK` で通過しつつ、危険な User-Agent、XSS / SQLi を疑う query、非許可メソッドに対しては `403` または `405` を返すことを確認した。  
この結果から、`NIPS` を広域的な侵入防止層、`WAF` を Web アプリケーション通信に特化した詳細防御層として分離して説明できる。

## 注意

これは `ModSecurity + OWASP CRS` の本格 WAF ではなく、その前段の簡易ガードです。
学習用・構成確認用としては十分ですが、本番では dedicated WAF ルールセットへ置き換える前提です。

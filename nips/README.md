# NIPS

このディレクトリには、`NIPS` の設計と実装を置きます。

## NIPS とは何か

`NIPS` は `Network Intrusion Prevention System` の略で、通信を監視するだけでなく、不正と判断したトラフィックを通信本線上で遮断できる層です。

今回の構成で重要なのは、`NIDS` と `NIPS` を分けて考えることです。

- `NIDS`
  - 検知が中心
  - 通信を横から観測する
- `NIPS`
  - 検知に加えて遮断も行う
  - 通信本線上に置く

つまり `NIPS` は、単なる監視ではなく「危険な通信をその場で止める」役割を持ちます。

## 今回の構成における位置づけ

ルート README にある最終構成では、`NIPS` は `External Firewall` の後段、`WAF` の前段にあります。

現在、外部公開される入口はHTTPS用`443`だけです。HAProxy設定内にHTTP用frontendが残っていても、External Firewallおよびホストからポート`80`へ到達する経路はありません。外部疎通確認は`https://`を使用し、TLS ClientHello検査を経由します。

```text
Client
  -> External Firewall
  -> NIPS
  -> WAF
  -> Reverse Proxy
  -> Internal Firewall
  -> API Gateway / Backend
  -> Database
```

この配置の意味は次の通りです。

- `External Firewall`
  - まず到達性そのものを絞る
- `NIPS`
  - 通信本線上で不正通信を止める
- `WAF`
  - HTTP/HTTPS の中身をさらに詳細に検査する

したがって `NIPS` は、外周で許可された通信のうち「通してよいように見えるが、内容や振る舞いとして危険なもの」を止める中間防御層として考えます。

## 今回の NIPS が見るべき要素

今回の方針では、`NIPS` は L3 から L7 までの情報を総合的に見て判断するものとして整理します。

### L3

- IP の経路や到達元
- 異常な送信元分布
- DDoS を疑う大量トラフィック

例:

- 明らかに異常な送信元分布
- 単位時間あたりの急激な接続増加
- 到達元の偏りや flooding

### L4

- TCP / UDP の通信状態
- SYN flood
- セッション異常
- セッションハイジャックを疑う不自然な振る舞い

例:

- SYN の急増
- 接続確立失敗の偏り
- 不自然な再送や状態遷移

### L5 / L6

- セッションの張り方
- TLS や暗号化周辺の異常
- 通信の確立手順の不自然さ

今回の段階では、この層は次のように整理します。

- 暗号化 handshake の異常
- 通常と異なるセッション確立パターン
- 不自然なプロトコル利用

ここは L3/L4 や L7 より説明が抽象的になりやすいため、報告書では「暗号化異常やセッション確立異常を補助的に見る層」と書くのが実務的です。

### L7

- SQL Injection
- XSS
- 明らかな不正通信
- アプリケーションレベルの攻撃パターン

例:

- 攻撃シグネチャに一致するリクエスト
- 通常 API 利用から外れた不審なリクエスト
- 明らかな exploit 試行

## NIPS と WAF の違い

`NIPS` と `WAF` は似て見えますが、役割は同じではありません。

- `NIPS`
  - より広い層を見る
  - L3 から L7 まで総合的に扱う
  - ネットワーク境界で不正通信全般を止める
- `WAF`
  - 主に HTTP/HTTPS に特化する
  - Web アプリケーション攻撃を重点的に見る

今回の設計では、`NIPS` は「広く止める層」、`WAF` は「Web に深く効く層」として分けて説明できます。

## 今回のリポジトリでの扱い

現時点では `NIPS` を Docker Compose の直列サービスとして実装しています。  
今回の実装は `HAProxy` を使った inline NIPS で、`external-firewall` の後段、`WAF` の前段に配置します。

現在の本線:

```text
Client
  -> External Firewall
  -> NIPS
  -> WAF
  -> Reverse Proxy
  -> Internal Firewall
  -> Backend
  -> Database
```

つまり現段階の位置づけは次の通りです。

- 実装済み
  - `External Firewall`
  - `NIPS`
  - `Reverse Proxy`
  - `Internal Firewall`
  - `Backend`
  - `Database`
- 構想・次段階
  - `API Gateway`
  - `NIDS`
  - `HIDS/HIPS`

## 実装方針の候補

今回の実装では、次の方針を選びます。

- `External Firewall` と `Reverse Proxy` の間に inline で置く
- `HAProxy` を使って本線上で reject / deny を返す
- シグネチャベースと振る舞いベースを組み合わせる

## 今回の実装内容

設定ファイル:

- `haproxy.cfg`
- `haproxy.pass-through.cfg`

使用イメージ:

- `haproxy:2.9-alpine`

## NIPS が実際にどう動くか

今回の `NIPS` は `HAProxy` を使った inline proxy として動きます。  
つまり、ミラー監視ではなく、通信が必ずこのコンテナを通る構造です。

通信の流れは次の通りです。

```text
Client
  -> External Firewall
  -> NIPS(HAProxy)
  -> WAF
  -> Reverse Proxy
  -> Application
  -> Backend
```

ここで重要なのは、`NIPS` が「横から見る監視装置」ではなく、「通すか止めるかをその場で決める関所」だという点です。

### 1. まず External Firewall から NIPS に届く

最初に、公開入口である`External Firewall`がHTTPS用`443`のみを受けます。
その後、許可された通信だけが `nips` コンテナへ転送されます。

つまり `NIPS` は、外部から来る全 Web 通信の最初の検査地点です。

### 2. HAProxy が 80 番と 443 番を別々に受ける

`haproxy.cfg` では frontend を 2 つ定義しています。

- `frontend http_in`
  - `bind :80`
  - 平文 HTTP 用
- `frontend https_in`
  - `bind :443`
  - TLS 通信用

この分離により、HTTP と HTTPS で見られる情報の違いをそのまま実装に反映しています。

### 3. HTTP は L7 まで見ながら判定する

`http_in` では、送信元 IP ごとの状態を `stick-table` で記録しつつ、HTTP リクエスト自体も見ています。

具体的には次を追跡します。

- `conn_rate(10s)`
  - 10 秒あたりの接続増加
- `conn_cur`
  - 現在の同時接続数
- `http_req_rate(10s)`
  - 10 秒あたりの HTTP リクエスト数

動きとしては次の通りです。

1. `http-request track-sc0 src`
   - 送信元 IP を table に記録する
2. `acl src_conn_abuse ...`
   - 接続レート異常を判定する
3. `acl src_conn_cur_abuse ...`
   - 同時接続数異常を判定する
4. `acl src_req_abuse ...`
   - 短時間の過剰リクエストを判定する
5. `http-request deny deny_status 429 if ...`
   - 異常ならその場で `429` を返して止める

つまり HTTP 側では、

- flood 的な振る舞い
- 明らかな多接続
- 過剰な短時間アクセス

を `NIPS` 自体が本線上で遮断します。

### 4. HTTPS はまず TLS として妥当かを見る

`https_in` は `mode tcp` で動いています。  
これは、HTTPS payload をここで復号して精査するのではなく、まず TCP/TLS の入口として不自然でないかを見るためです。

ここで行っていることは次です。

1. `tcp-request connection track-sc0 src`
   - 送信元 IP を追跡する
2. `acl tls_conn_abuse ...`
   - 443 に対する接続レート異常を見る
3. `acl tls_conn_cur_abuse ...`
   - 同時接続の異常を見る
4. `acl client_hello req.ssl_hello_type 1`
   - TLS ClientHello として妥当かを見る
5. `tcp-request connection reject if ...`
   - 接続異常なら拒否する
6. `tcp-request content reject if !client_hello`
   - TLS として不自然なら拒否する

つまり HTTPS 側では、

- 443 に大量接続してくる不審な振る舞い
- TLS handshake として成立していない不自然な通信

を早い段階で止めています。

### 5. 問題がなければ WAF に渡す

`NIPS` は最終到達点ではなく、中継しながら判定する層です。  
異常が無い通信だけを後段 API に渡します。

現在の後段転送先は次の 2 つです。

- `backend waf_http`
  - `server waf waf:80`
- `backend waf_https`
  - `server waf waf:443`

したがって、

- HTTP は `waf:80`
- HTTPS は `waf:443`

へ流れ、以降の Web 向け詳細検査は `WAF` が担当します。

### 6. どこで止まったかの考え方

今回の `NIPS` では、挙動を見ると大まかに次のように判断できます。

- `429`
  - 接続数やリクエストレートが閾値を超えた
- TCP レベルで切断
  - TLS handshake 異常、または 443 側の接続異常
- 正常に次へ進む
  - `WAF` へ転送された

つまり、`NIPS` はアプリのレスポンスを返す装置ではなく、「入口で落とすか、次へ通すか」を決める装置として動いています。

### 7. pass-through では何が変わるか

`haproxy.pass-through.cfg` では、通常版にある遮断判定を外しています。

そのため pass-through 時は次の動きになります。

- `NIPS` コンテナ自体は本線上に残る
- ただし `stick-table` による遮断や TLS 異常拒否を行わない
- 受けた通信をそのまま `WAF` に流す

これにより、

- 通常 NIPS
  - 早い段階で広く止める
- pass-through NIPS
  - 入口では止めず、後段へ流す

という差分を比較できます。

### 8. この実装をどう説明すればよいか

報告書では、次のように書くと整理しやすいです。

`NIPS`は`HAProxy`を用いたinline proxyとして実装し、External Firewallを通過した`443`のTLS通信を受ける。TCP/TLSの入口で接続レート、同時接続数、TLS ClientHelloの妥当性を確認し、不自然な通信を拒否する。正常な通信のみを後段の`WAF`に渡すことで、`NIPS`は広域的な侵入防止層、`WAF`はWebアプリケーション向けの詳細防御層として役割分担している。

### L3 / L4 で見ているもの

今回の `NIPS` では、送信元 IP 単位の stick-table を使って、次のような異常を見ます。

- 接続レートの異常増加
- 同時接続数の異常増加
- 短時間の過剰リクエスト

具体的には次のように扱います。

- HTTP 側
  - `conn_rate(10s)`
  - `conn_cur`
  - `http_req_rate(10s)`
- HTTPS 側
  - `conn_rate(10s)`
  - `conn_cur`

これにより、DDoS 的な急増や flood を疑う振る舞いを、送信元単位で早期に遮断します。

### L5 / L6 で見ているもの

HTTPS 側では `req.ssl_hello_type 1` を使い、TLS ClientHello として不自然な通信を拒否します。

つまり今回の段階では、L5/L6 相当として次を見ています。

- 443 番ポートに来た通信が TLS ClientHello として妥当か
- TLS 確立前の不自然な通信ではないか

### L7 で見ているもの

HTTP 側では、平文の HTTP リクエストに対して次のパターンを deny します。

- 危険な User-Agent
  - `sqlmap`, `nikto`, `nmap`, `dirbuster`, `gobuster`, `wpscan`
- 危険な path
  - `../`, `/etc/passwd`, `/wp-admin`, `/phpmyadmin` など
- 危険な query
  - `union select`, `sleep()`, `benchmark()`, `<script>`, `or 1=1` など

### 実装上の限界

今回の `NIPS` は本線上で動作しますが、HTTPS の payload を復号して深く検査しているわけではありません。  
そのため、443 番ポートに対する深い L7 検査は将来の `WAF` 側に委ねます。

つまり現段階では次の分担です。

- `NIPS`
  - L3/L4 の振る舞い
  - TLS handshake の妥当性
  - HTTP 平文の基本的な L7 シグネチャ
- `WAF`
  - Web アプリケーション通信の詳細検査

## なぜ HAProxy を使うのか

今回必要なのは、単なる中継ではなく「本線上で見て、その場で止める」ことです。  
`HAProxy` は次の点で今回の NIPS に適しています。

- inline proxy として経路上に置きやすい
- source IP 単位の rate / conn 制御ができる
- TCP と HTTP の両方を扱える
- deny / reject をその場で返せる

`Suricata` や `Snort` の方が専用 NIPS としては強力ですが、今回の単一 VM / Docker 検証では、まず inline に成立する NIPS を作ることを優先しています。

## 実測した検証結果

今回の実装では、`external-firewall -> nips -> waf -> reverse-proxy` の本線に NIPS を差し込み、正常疎通と遮断の両方を確認しました。

### 正常系

確認結果:

- `http://127.0.0.1/health`
  - `200 OK`
- `https://127.0.0.1/api/health`
  - `200 OK`

意味:

- NIPS を経由しても正規トラフィックは FastAPI まで通る
- `External Firewall -> NIPS -> WAF -> Reverse Proxy -> Application -> Backend` の本線が成立している

### 遮断系

確認結果:

- `curl -A "sqlmap" http://127.0.0.1/`
  - `403`
- `curl "http://127.0.0.1/?q=<script>"`
  - `403`
- `curl "http://127.0.0.1/?q=union%20select"`
  - `403`

意味:

- 危険な User-Agent は NIPS で遮断できた
- XSS 風 query は NIPS で遮断できた
- SQLi 風 query は NIPS で遮断できた

### 今回の実装で確認できたこと

- L3/L4 的な source 単位の rate / connection 制御の土台が入った
- L5/L6 的な TLS ClientHello 妥当性チェックを 443 番ポートで行える
- L7 では、少なくとも平文 HTTP に対して基本的な攻撃シグネチャ遮断が機能した

### 今回の実装で残る限界

- HTTPS payload の深い L7 検査はまだ行っていない
- 443 番ポートでは TLS handshake 妥当性までは見ているが、復号後の詳細検査は `WAF` 側の役割として残る
- したがって、この NIPS は「広く止める」層であり、「Web に深く効く」層は将来の WAF で補完する前提である

## 有効化・無効化と比較方法

### 有効化

`NIPS` を有効にした状態で本線を起動する:

```bash
docker compose up -d nips external-firewall reverse-proxy
```

必要なら設定変更を反映して再作成する:

```bash
docker compose up -d --force-recreate nips external-firewall
```

状態確認:

```bash
docker compose ps nips external-firewall reverse-proxy
docker compose logs --tail=50 nips
```

### 無効化

`NIPS` だけを止める:

```bash
docker compose stop nips
```

再開:

```bash
docker compose start nips
```

### 比較の考え方

比較したいのは次の 2 点です。

- 正常トラフィックは通るか
- 広域的に危険と判断した通信を NIPS が止めるか

### NIPS 有効時の確認コマンド

```bash
curl -i http://127.0.0.1/health
curl -k -i https://127.0.0.1/api/health
curl -i -A "sqlmap" http://127.0.0.1/
curl -i "http://127.0.0.1/?q=<script>"
curl -i "http://127.0.0.1/?q=union%20select"
```

期待値:

- `/health`
  - `200`
- `/api/health`
  - `200`
- `sqlmap`
  - `403`
- XSS / SQLi 風 query
  - `403`

### NIPS 無効時の見え方

`nips` を止めると、本線は `external-firewall -> nips -> waf ...` の途中で切れます。

そのため、今回の構成では次を確認できます。

- 正常な `/health` や `/api/health` も通らなくなる
- `NIPS` が単なる観測点ではなく、本線上の遮断・中継要素であることが分かる

### 検査だけを無効化して比較する方法

`stop` だと経路自体が切れるため、「NIPS が無いと攻撃通信がどう見えるか」を比較しにくいです。  
そのため、このリポジトリでは pass-through 設定も用意しています。

pass-through で再起動する:

```bash
docker compose -f docker-compose.yml -f docker-compose.nips-bypass.yml up -d --force-recreate nips
```

通常の NIPS に戻す:

```bash
docker compose up -d --force-recreate nips
```

pass-through 時の意味:

- `NIPS` コンテナ自体は本線上に残る
- ただし rate 制御や TLS ClientHello 検査、HTTP deny を行わない
- そのため通信は `WAF` まで素通しされる

### pass-through 比較で確認できること

通常 NIPS:

- flood 的な急増や不自然な TLS 通信を早い段階で止める
- 平文 HTTP の明らかな不正パターンを `403/429` で止める

pass-through NIPS:

- NIPS 自体では止めず、後段の `WAF` やさらに先へ流れる
- 「NIPS が広く早く止める層」であることを比較しやすい

### 何が比較できるか

- `NIPS` 有効時
  - 正常通信は通る
  - 広域的に危険な HTTP 通信は `403`
- `NIPS` 無効時
  - 入口から後段へ流れなくなり、正常通信も成立しない

つまり、この比較により `NIPS` が「あるときだけ攻撃を止める追加ルール」ではなく、本線上で通信判定と遮断を担う inline 要素であることを説明できます。  
また、pass-through 比較を使うと、「NIPS を止めたので経路が切れた」のではなく、「NIPS の検査を外したので後段まで通った」という説明ができます。

## 報告書向けのまとめ

今回の NIPS は、`HAProxy` を用いて `External Firewall` と `Reverse Proxy` の間に inline で配置した。  
これにより、正常な HTTP/HTTPS トラフィックを FastAPI まで通しつつ、危険な User-Agent や XSS / SQLi を疑う HTTP リクエストを `403` で遮断できることを確認した。  
また、443 番ポートでは TLS ClientHello の妥当性確認を行うことで、少なくとも暗号化セッション確立前の異常通信を拒否する基盤を持たせた。  
一方で、HTTPS payload の深い L7 検査はまだ行っておらず、この部分は将来の WAF によって補完する想定である。

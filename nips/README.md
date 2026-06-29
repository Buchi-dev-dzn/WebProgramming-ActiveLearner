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
今回の実装は `HAProxy` を使った inline NIPS で、`external-firewall` と `reverse-proxy` の間に配置します。

現在の本線:

```text
Client
  -> External Firewall
  -> NIPS
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
  - `WAF`
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

使用イメージ:

- `haproxy:2.9-alpine`

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

今回の実装では、`external-firewall -> nips -> reverse-proxy` の本線に NIPS を差し込み、正常疎通と遮断の両方を確認しました。

### 正常系

確認結果:

- `http://127.0.0.1/health`
  - `200 OK`
- `https://127.0.0.1/api/health`
  - `200 OK`

意味:

- NIPS を経由しても正規トラフィックは backend まで通る
- `External Firewall -> NIPS -> Reverse Proxy -> Application -> Backend` の本線が成立している

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

## 報告書向けのまとめ

今回の NIPS は、`HAProxy` を用いて `External Firewall` と `Reverse Proxy` の間に inline で配置した。  
これにより、正常な HTTP/HTTPS トラフィックを backend まで通しつつ、危険な User-Agent や XSS / SQLi を疑う HTTP リクエストを `403` で遮断できることを確認した。  
また、443 番ポートでは TLS ClientHello の妥当性確認を行うことで、少なくとも暗号化セッション確立前の異常通信を拒否する基盤を持たせた。  
一方で、HTTPS payload の深い L7 検査はまだ行っておらず、この部分は将来の WAF によって補完する想定である。

## 報告書向けのまとめ

今回の `NIPS` は、外周で許可された通信に対して、L3 から L7 までの情報を総合的に見ながら、不正と判断した通信を遮断する侵入防止層として位置づける。  
今回の実装では `HAProxy` を inline に配置し、送信元 IP ごとの異常な接続レートや TLS handshake 異常、HTTP レベルの明らかな攻撃シグネチャを本線上で遮断する。  
`External Firewall` が到達性そのものを制御し、将来の `WAF` が Web アプリケーション通信をさらに詳細に検査するのに対し、`NIPS` はその中間でネットワーク境界全体を対象に広く不正通信を止める役割を担う。

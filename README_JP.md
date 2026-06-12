# インフラ構成と host-firewall 確認メモ

このリポジトリは、WAF、リバースプロキシ、バックエンド API、DB、Redis などを含む検証用インフラ構成です。

## 主な構成

- `waf`
  - `80` / `443` を公開する Nginx
- `reverse-proxy`
  - 内部向け Nginx
- `backend-api`
  - Node.js アプリ
- `postgres`
  - PostgreSQL
- `redis`
  - Redis
- `host-firewall`
  - ホスト側で `nftables` または `XDP/eBPF` により L3/L4 フィルタを適用するための Rust 実装
- `firewall-lab`
  - TCP 到達性を確認するための Rust プローブ

## Docker と host-firewall の役割分担

この構成のアプリ本体は Docker Compose で動作します。  
一方で `host-firewall` は Docker コンテナではなく、Linux VM ホスト上の `nftables` または `XDP/eBPF` にルールを入れる実装です。

整理すると次のとおりです。

- Docker Compose
  - `waf`, `reverse-proxy`, `backend-api`, `postgres`, `redis` などを起動する
- Docker の port publish
  - たとえば `waf` は `80:80`, `443:443` を公開する
  - `log-viewer` は `monitoring` profile 有効時に `3001:3000` を公開する
- `host-firewall`
  - その publish されたポートに到達する前に、ホスト境界で `DROP` / `ACCEPT` を判断する

つまり、Docker 側に公開設定があっても、host firewall が落とせば外部からは到達できません。  
今回 `3001` が外部からタイムアウト相当だったのは、この境界で遮断できていることを示します。

## なぜ Firewall が先に効くのか

今回の Ubuntu VM では、Mac などの外部クライアントはまず Ubuntu host の IP に向けて接続します。  
そのため、Docker コンテナそのものより先に、ホスト OS 側の `nftables` / `XDP` が入口で判定できます。

今の構成で外から見えるのは主に次です。

- `22`
  - Ubuntu host 自身の SSH
- `80`, `443`
  - `waf` を host に publish したポート
- `3001`
  - `monitoring` profile を有効にしたときだけ publish されるポート

逆に、次は外から直接は見えません。

- `backend-api:8080`
- `postgres:5432`
- `redis:6379`
- `reverse-proxy:80`

これらは `ports:` ではなく `expose:` や内部 network で使われているためです。  
したがって、外部クライアントが最初に触れるのは host の公開ポートであり、その段階で host firewall を適用できます。

ただし、この性質は構成依存です。  
将来 backend を直接 `ports:` で公開したり、Docker の転送経路を変えたりする場合は、`input` chain だけでなく `FORWARD` や `DOCKER-USER` 側も含めて設計した方が安全です。

## host-firewall の想定動作

`host-firewall` の README では、ホワイトリスト方式で以下のポートのみを許可する想定です。

- `22`
- `80`
- `443`

この想定では、たとえば `3001` は Firewall により遮断され、到達確認ではタイムアウト相当になることが期待されます。

加えて、`nftables` backend では一時的な IP ブラックリストも扱えます。

- 静的な拒否: `--deny-src`
- timeout 付きの一時拒否: `--temp-deny-src`, `--ban-src`, `--unban-src`

たとえば、過度に接続してくる特定 IP を 30 分だけ遮断したい場合は次のように使えます。

```bash
cd /home/buchi/infra/host-firewall
sudo cargo run -- --backend nft --ban-src 198.51.100.7 --temp-deny-timeout 30m
```

解除は次です。

```bash
cd /home/buchi/infra/host-firewall
sudo cargo run -- --backend nft --unban-src 198.51.100.7
```

この一時ブラックリストは Docker コンテナ内ではなく、ホスト側の `nftables` set として管理されます。

さらに、自動 ban も有効化できます。

```bash
cd /home/buchi/infra/host-firewall
sudo cargo run -- --backend nft --apply \
  --auto-ban \
  --auto-ban-threshold 50 \
  --auto-ban-window 10s \
  --auto-ban-timeout 30m
```

この設定では、同一 IP から 10 秒間に 50 回を超える新規 TCP 接続が来た場合、その IP を 30 分間 ban します。
これは WAF 的な HTTP 内容検査ではなく、host firewall の入口制御としての挙動です。

注意点として、この自動 ban が見ているのは HTTP リクエスト数ではなく、新規 TCP 接続数です。

## 実施した確認

### コンテナ状態

確認時点では以下の主要コンテナは起動していました。

- `waf`
- `reverse-proxy`
- `backend-api`
- `postgres`
- `redis`

### ホストの待受ポート

確認できた待受ポート:

- `22`
- `80`
- `443`

`3001` の待受は確認できませんでした。

### 同一ホストからの TCP プローブ結果

`firewall-lab` で `127.0.0.1` に対して `22,80,443,3001` を確認した結果:

```text
port=22 outcome=allowed
port=80 outcome=allowed
port=443 outcome=allowed
port=3001 outcome=reachable_but_no_listener
```

この結果だけでは Firewall の有効性は判定できません。  
同一ホストから自分自身の IP や loopback に接続すると、ローカル配送の影響で `iif "lo" accept` に乗り、期待した drop を観測できないことがあるためです。

### 別マシンからの確認

Mac から `192.168.64.4` に対して `nc` で確認した結果:

- `80`: 接続成功
- `443`: `Connection refused`
- `3001`: ハングし、タイムアウト相当

さらに Linux 側では、`nftables` に次の入力ルールが適用されていることを確認しました。

- `policy drop`
- `tcp dport { 22, 80, 443 } accept`
- `iif "lo" accept`
- `ct state established,related accept`

## 判定

`host-firewall` の `nftables` backend は、外部からの到達性確認という観点では概ね期待どおりに動作しています。

理由:

- `80` は外部クライアントから到達できた
- `3001` は外部クライアントからタイムアウト相当になった
- `nftables` の input chain には `policy drop` と `tcp dport { 22, 80, 443 } accept` が入っていた

つまり、「許可ポートは通し、それ以外は落とす」という host firewall の主目的は満たしていると判断できます。

また、今後は一時ブラックリスト set を使うことで、「80 は通常は通すが、特定 IP だけ 30 分落とす」といった運用も可能です。

一方で `443` は別問題です。

- Mac からは `Connection refused`
- Linux 側の `curl -kI https://127.0.0.1` は `Broken pipe`

これは Firewall で遮断されたのではなく、`waf` の HTTPS 待受や TLS 設定に問題がある可能性を示します。

## 制約事項

以下は引き続き未確認です。

- XDP プログラムの attach 状態
- `443` の HTTPS 設定不整合の原因

XDP 側は `sudo` と object build が必要です。  
`443` 側は `docker compose exec waf nginx -T` などで追加確認できます。

## 関連ファイル

- [host-firewall-check-ja.md](/home/buchi/infra/host-firewall-check-ja.md)
- [docker-compose.yml](/home/buchi/infra/docker-compose.yml)
- [host-firewall/README.md](/home/buchi/infra/host-firewall/README.md)
- [firewall-lab/README.md](/home/buchi/infra/firewall-lab/README.md)

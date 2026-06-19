# host-firewall 検証メモ

このメモは、Mac から Linux VM 上の `host-firewall` 実装を実機確認するための手順をまとめたものです。

## 前提

- Linux VM の外向け IP は `192.168.64.4`
- Host Firewall の許可ポートは `22,80,443`
- `3001` は非許可ポートとして比較に使う
- Docker Compose 構成上、外部から意味がある確認対象は主に `22`, `80`, `443`, `3001`

補足:

- `80`, `443` は `waf` コンテナが host に publish している
- `3001` は `log-viewer` 用で、monitoring profile を有効にした場合に publish される
- `8080`, `5432`, `6379` は外部公開していないため、今回の host-firewall 比較対象には向かない

## 目的

確認したいのは次の比較です。

1. Firewall を付けていないときと、付けているときで挙動が変わるか
2. 許可ポートと非許可ポートで挙動が分かれるか

期待する方向性:

- 許可ポート `22`, `80`, `443` は通る
- 非許可ポート `3001` は Firewall 有効時に `timeout` になる

## 使う IP

VM 側で確認した IP:

```text
192.168.64.4
```

Mac からはこの IP に対してアクセスする。

## 事前確認

VM 側で Docker サービスが起動していることを確認する。

```bash
cd ~/infra
docker compose ps
```

必要に応じて起動:

```bash
cd ~/infra
docker compose up -d
```

## backend ごとの動き

`host-firewall` には `nft` と `xdp` の 2 backend がある。

- `nft`
  - `nftables` の `input` chain にルールを入れる
  - stateful
  - 今回の確認はまずこちらを使うのが分かりやすい
- `xdp`
  - NIC 受信直後に eBPF/XDP で port whitelist を判定する
  - stateless
  - `nft` より低いレイヤーで動く

通常の検証では、まず `nft` backend だけで十分。

## 操作の基本

CLI の基本形はこれ。

```bash
cd ~/infra/host-firewall
cargo run -- --backend nft
sudo /home/buchi/.cargo/bin/cargo run -- --backend nft --apply
sudo /home/buchi/.cargo/bin/cargo run -- --backend xdp --apply
sudo /home/buchi/.cargo/bin/cargo run -- --backend xdp --apply --detach
```

意味:

- `cargo run -- --backend nft`
  - dry-run
  - 実際には適用せず、入る予定の nftables ルールを表示する
- `sudo /home/buchi/.cargo/bin/cargo run -- --backend nft --apply`
  - `nftables` ルールを実適用する
- `sudo /home/buchi/.cargo/bin/cargo run -- --backend xdp --apply`
  - XDP program を attach する
- `sudo /home/buchi/.cargo/bin/cargo run -- --backend xdp --apply --detach`
  - XDP program を detach する

補足:

- `cargo` 自体は入っていても、`sudo` の `PATH` に `~/.cargo/bin` が入っていない環境では `sudo cargo ...` が失敗する
- その場合は `sudo /home/buchi/.cargo/bin/cargo ...` のようにフルパスで実行する

## 無効化の考え方

### nft backend の無効化

今の実装には `nft` 用の `--detach` はない。
そのため、無効化は nftables table を手動で削除する。

コマンド:

```bash
sudo nft delete table inet codex_host_filter
```

削除後の確認:

```bash
sudo nft list tables
```

`codex_host_filter` が出なければ無効化できている。

### xdp backend の無効化

`xdp` は CLI から detach できる。

```bash
cd ~/infra/host-firewall
sudo /home/buchi/.cargo/bin/cargo run -- --backend xdp --apply --detach
```

これは

- `ip link set dev <iface> xdp off`
- pinned program の削除

をまとめて行う。

## 典型的な検証の流れ

一番分かりやすいのは次の順番。

1. Docker サービスを起動する
2. Firewall を無効状態にする
3. Mac から `22/80/443/3001` を確認する
4. Firewall を有効化する
5. 同じ確認をもう一度行う
6. 許可ポートと非許可ポートの差を見る

## Firewall を無効状態にする手順

### nft を使っている場合

```bash
sudo nft delete table inet codex_host_filter
sudo nft list tables
```

### xdp を使っている場合

```bash
cd ~/infra/host-firewall
sudo /home/buchi/.cargo/bin/cargo run -- --backend xdp --apply --detach
```

注意:

- `nft` と `xdp` を両方有効にすると比較がややこしくなる
- 最初の確認は `nft` だけで進める方が安全

## Mac からの確認コマンド

### L4 到達性確認

```bash
nc -vz 192.168.64.4 22
nc -vz 192.168.64.4 80
nc -vz 192.168.64.4 443
nc -vz -w 3 192.168.64.4 3001
```

### HTTP/HTTPS 確認

```bash
curl -i http://192.168.64.4/health
curl -i http://192.168.64.4/
curl -k -i https://192.168.64.4/
```

### WAF の簡易確認

```bash
curl -i -A "sqlmap" http://192.168.64.4/
curl -i "http://192.168.64.4/?q=<script>"
curl -i -X PUT http://192.168.64.4/
```

期待値:

- 危険な User-Agent は `403`
- 不審な query は `403`
- 非許可メソッドは `405`

## Firewall 無効時の確認

Mac で以下を実行する。

```bash
nc -vz 192.168.64.4 22
nc -vz 192.168.64.4 80
nc -vz 192.168.64.4 443
nc -vz -w 3 192.168.64.4 3001

curl -i http://192.168.64.4/health
curl -i http://192.168.64.4/
curl -k -i https://192.168.64.4/
```

見方:

- `succeeded`: 到達していて待受もある
- `Connection refused`: Firewall は通っているが、そのポートで待受していない
- `timeout`: 途中で drop されている可能性が高い

無効時の想定:

- `22`: success
- `80`: success
- `443`: success または refused
- `3001`: success または refused

## Firewall 有効化

VM 側で `nft` backend を適用する。

### dry-run

```bash
cd ~/infra/host-firewall
cargo run -- --backend nft
```

### apply

```bash
cd ~/infra/host-firewall
sudo /home/buchi/.cargo/bin/cargo run -- --backend nft --apply
```

### 適用確認

```bash
sudo nft list table inet codex_host_filter
```

確認したい要点:

- `policy drop`
- `iif "lo" accept`
- `ct state established,related accept`
- `tcp dport { 22, 80, 443 } accept`

## Firewall 無効化

### nft backend を外す

```bash
sudo nft delete table inet codex_host_filter
```

確認:

```bash
sudo nft list tables
```

### xdp backend を外す

```bash
cd ~/infra/host-firewall
sudo /home/buchi/.cargo/bin/cargo run -- --backend xdp --apply --detach
```

## すぐ使える往復手順

### 1. 無効化して baseline を取る

VM 側:

```bash
sudo nft delete table inet codex_host_filter
```

Mac 側:

```bash
nc -vz 192.168.64.4 22
nc -vz 192.168.64.4 80
nc -vz 192.168.64.4 443
nc -vz -w 3 192.168.64.4 3001
curl -i http://192.168.64.4/health
```

### 2. 有効化して比較する

VM 側:

```bash
cd ~/infra/host-firewall
sudo /home/buchi/.cargo/bin/cargo run -- --backend nft --apply
sudo nft list table inet codex_host_filter
```

Mac 側:

```bash
nc -vz 192.168.64.4 22
nc -vz 192.168.64.4 80
nc -vz 192.168.64.4 443
nc -vz -w 3 192.168.64.4 3001
curl -i http://192.168.64.4/health
```

### 3. 片付け

VM 側:

```bash
sudo nft delete table inet codex_host_filter
```

## Firewall 有効時の確認

Mac で、無効時と同じコマンドを再実行する。

```bash
nc -vz 192.168.64.4 22
nc -vz 192.168.64.4 80
nc -vz 192.168.64.4 443
nc -vz -w 3 192.168.64.4 3001

curl -i http://192.168.64.4/health
curl -i http://192.168.64.4/
curl -k -i https://192.168.64.4/
```

有効時の想定:

- `22`: success
- `80`: success
- `443`: success または refused
- `3001`: timeout

特に `3001` が

- 無効時は `refused` か `success`
- 有効時は `timeout`

に変わるかを見る。

## 比較結果のメモ用テンプレート

### Firewall 無効

```text
22:
80:
443:
3001:
/health:
/:
https /:
```

### Firewall 有効

```text
22:
80:
443:
3001:
/health:
/:
https /:
```

## 判定の考え方

`host-firewall` が期待どおりなら、少なくとも以下が成立する。

- 許可ポート `22`, `80`, `443` は外部クライアントから到達できる
- 非許可ポート `3001` は外部クライアントから timeout になる

注意点:

- `443` が `refused` の場合、Firewall ではなく HTTPS 待受設定の問題の可能性がある
- 同一ホスト内からの確認と、Mac からの確認では経路が違う
- Host Firewall の確認には `nc` が分かりやすく、WAF の確認には `curl` が向いている
- `nft` backend には専用 detach がないので、無効化は table 削除で行う
- `xdp` backend は detach コマンドがある

## 今回の環境での最短手順

Linux VM 側:

```bash
cd ~/infra/host-firewall
cargo run -- --backend nft
sudo /home/buchi/.cargo/bin/cargo run -- --backend nft --apply
sudo nft list table inet codex_host_filter
```

Mac 側:

```bash
nc -vz 192.168.64.4 22
nc -vz 192.168.64.4 80
nc -vz 192.168.64.4 443
nc -vz -w 3 192.168.64.4 3001
curl -i http://192.168.64.4/health
```

判定:

- `22`, `80`, `443` が通るのは正常
- `3001` が `timeout` になれば `host-firewall` の遮断が効いている
- `3001` が `refused` のままなら、待受が無いだけで firewall の効果はまだ確認できていない

## 参考

- [README.md](/home/buchi/infra/host-firewall/README.md)
- [../docker-compose.yml](/home/buchi/infra/docker-compose.yml)
- [../host-firewall-check-ja.md](/home/buchi/infra/host-firewall-check-ja.md)

# Host Firewall

このディレクトリは、Linux VM ホスト側で適用する Host Firewall 実装です。

## 方針

現状の `host-firewall` は、許可ポートのホワイトリストに特化した packet filter として扱います。

- デフォルト拒否
- 許可するのは指定した TCP 宛先ポートのみ
- `ICMP`, `ICMPv6` は疎通確認のため許可
- HTTP 内容、URL、source IP、rate limit は見ない

つまり責務は「ホスト入口で、開けるポートだけを選別する」ことです。

## 役割

- VM に入る通信を `22`, `80`, `443` などの必要ポートだけに絞る
- Docker に届く前の段階で不要な TCP ポートを落とす
- WAF より手前で L3/L4 の単純な入口制御を担う

## Docker との関係

このリポジトリでは Docker Compose がサービスを動かしますが、`host-firewall` 自体は Docker コンテナではなくホスト OS 側で動かします。

- `waf`
  - `80:80`, `443:443` を host に publish
- `log-viewer`
  - monitoring profile 時のみ `3001:3000` を publish
- `host-firewall`
  - host の `nftables` または `XDP` にルールを入れる
  - publish されたポートに届く前に不要通信を落とす

## backend

### `nft`

- `input` chain に `policy drop`
- `iif "lo"` を許可
- `ct state established,related` を許可
- `tcp dport { ... }` だけを許可
- `ICMP`, `ICMPv6` を許可

stateful な入口制御です。

### `xdp`

- NIC 受信直後で port whitelist を判定
- `TCP dport` のみを見る stateless filter
- `ICMP`, `ICMPv6` は許可
- non-IP traffic は `PASS`
- IPv6 extension header は未対応

`nftables` の完全互換ではなく、高速な pre-filter です。

## ファイル

- `src/main.rs`
  - CLI と backend 切り替え
- `src/nft.rs`
  - whitelist ルール生成と適用
- `src/xdp.rs`
  - XDP object の load / attach / detach
- `../host-firewall-common`
  - userspace と eBPF で共有する port whitelist 設定
- `../host-firewall-ebpf`
  - XDP/eBPF 本体
- `build-xdp.sh`
  - eBPF object のビルド補助

## 設定

運用値は [src/settings.rs](/home/buchi/infra/host-firewall/src/settings.rs:1) にまとめています。

- `TABLE_NAME`
- `ALLOWED_TCP_PORTS`
- `XDP_IFACE`
- `XDP_OBJECT`
- `XDP_PIN_PATH`
- `XDP_MODE`

通常の変更はこのファイルだけを書き換える想定です。

## CLI

CLI は操作だけを受けます。

- `--backend nft|xdp`
- `--apply`
- `--detach`

## 実行例

### nftables dry-run

```bash
cd /home/buchi/infra/host-firewall
cargo run -- --backend nft --allowed-tcp-ports 22,80,443
```

### nftables apply

```bash
cd /home/buchi/infra/host-firewall
sudo cargo run -- --backend nft --apply
sudo nft list table inet codex_host_filter
```

期待する要点:

- `policy drop`
- `iif "lo" accept`
- `ct state established,related accept`
- `tcp dport { 22, 80, 443 } accept`

### XDP dry-run

```bash
cd /home/buchi/infra/host-firewall
./build-xdp.sh
cargo run -- --backend xdp
```

### XDP apply

```bash
cd /home/buchi/infra/host-firewall
sudo cargo run -- --backend xdp --apply
ip link show dev eth0
```

### XDP detach

```bash
cd /home/buchi/infra/host-firewall
sudo cargo run -- --backend xdp --apply --detach
```

## 疎通確認

`firewall-lab` で確認します。

```bash
cd /home/buchi/infra/firewall-lab
cargo run -- --target 192.168.64.4 --ports 22,80,443,3001
```

別マシンから確認したい場合は `nc` でも十分です。

```bash
nc -vz 192.168.64.4 22
nc -vz 192.168.64.4 80
nc -vz 192.168.64.4 443
nc -vz -w 3 192.168.64.4 3001
```

見方:

- 接続成功: 許可され、待受もある
- `Connection refused`: ホストには届いたが待受がない
- timeout: firewall などで落ちている可能性が高い

## 注意

- 今の実装は source IP allow/deny を持ちません
- 一時 blacklist や auto-ban も持ちません
- HTTP レイヤの検査は `infra/waf/` の責務です
- backend を今後 host publish するなら、`input` だけでなく `FORWARD` や `DOCKER-USER` も再検討が必要です

# Firewall Lab

このディレクトリには、L3/L4 の Firewall 動作確認に寄せた Rust 製の TCP プローブを置いています。

## 何を確認するか

Firewall は HTTP レスポンスを返すものではありません。
ここで見たいのは、対象ホストの IP アドレスと TCP ポート番号に対して:

- TCP 接続が成立するか
- 接続拒否になるか
- タイムアウトするか

のどれになるかです。

## 判定基準

- `allowed`
  - TCP ハンドシェイクが完了した
  - Firewall がそのポートを通した
- `reachable_but_no_listener`
  - ホストまでは到達したが、そのポートでプロセスが待受していない
  - Firewall は通している
- `blocked_or_dropped`
  - タイムアウトした
  - Firewall や途中のパケットフィルタで落とされている可能性が高い

## 実行例

ローカルに Rust がある場合:

```bash
cd /home/buchi/infra/firewall-lab
cargo run -- --target 192.168.64.4 --ports 22,80,443,3001
```

Rust がローカルにない場合は Docker でも実行できます:

```bash
docker run --rm \
  -v /home/buchi/infra/firewall-lab:/app \
  -w /app \
  rust:1.79 \
  cargo run -- --target 192.168.64.4 --ports 22,80,443,3001
```

## 期待する見え方

Host Firewall をホワイトリスト方式で `22,80,443` のみにした場合:

- `22`
  - `allowed` または `reachable_but_no_listener`
- `80`
  - `allowed`
- `443`
  - `allowed` または `reachable_but_no_listener`
- `3001`
  - `blocked_or_dropped`

`3001` が `allowed` になった場合は、Firewall が未適用か、別ルールに負けています。

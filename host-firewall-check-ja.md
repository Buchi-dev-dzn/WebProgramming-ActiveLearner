# host-firewall 動作確認結果

確認日: 2026-06-05

## 結論

現時点では `host-firewall` は期待どおりには動作していません。

理由:

- `22`, `80`, `443` は接続成功しました。
- `3001` は `blocked_or_dropped` ではなく `reachable_but_no_listener` でした。
- README の期待値では、`22/80/443` のみ許可する host firewall が有効なら `3001` は遮断され、タイムアウト相当になるはずです。

したがって、少なくとも確認時点のホストでは `host-firewall` の遮断ルールは有効化されていないか、期待したルールで適用されていません。

## 実施内容

### 1. コンテナ状態確認

`docker compose ps` の結果:

- `backend-api`: Up
- `postgres`: Up
- `redis`: Up
- `reverse-proxy`: Up
- `waf`: Up

`waf` は以下を公開していました。

- `0.0.0.0:80->80/tcp`
- `0.0.0.0:443->443/tcp`

### 2. 待受ポート確認

`ss -ltn` の結果:

- `0.0.0.0:22`
- `0.0.0.0:80`
- `0.0.0.0:443`

`3001` の待受は確認できませんでした。

### 3. L4 プローブ確認

実行コマンド:

```bash
cd /home/buchi/infra/firewall-lab
cargo run -- --target 127.0.0.1 --ports 22,80,443,3001
```

結果:

```text
port=22 outcome=allowed
port=80 outcome=allowed
port=443 outcome=allowed
port=3001 outcome=reachable_but_no_listener
```

## 解釈

`3001` がタイムアウトせず、ホストから応答が返っているため、Firewall によるドロップは確認できませんでした。

この結果から言えること:

- `3001` でアプリは待受していない
- しかし `host-firewall` が `3001` を明示的に遮断している状態も確認できない

## 補足

`cargo test` は `host-firewall` と `firewall-lab` の両方で成功しましたが、どちらもテスト件数は `0` 件でした。
そのため、今回の判断はビルド成功ではなく実ポート確認の結果に基づいています。

## 未確認事項

- `nftables` の実テーブル内容
- XDP の attach 状態

これらは `sudo` による確認が必要でしたが、この環境では対話的な認証が必要で確認できませんでした。

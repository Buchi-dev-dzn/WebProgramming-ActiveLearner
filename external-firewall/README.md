# External Firewall Layer

このディレクトリには、`waf` より手前に置く `external-firewall` の設定を置いています。

## 役割

- 外部から入る `80/443` の TCP 接続を最初に受ける
- 送信元は `Any` とし、宛先は `80/443` だけを後段の `waf` に転送する
- HTTP レイヤの検査は行わず、L3/L4 ホワイトリスト型の外周境界として振る舞う

## 現在の実装

- カーネル層
  - `nftables` による host input policy
  - `80/443` 以外を先に落とすための companion 実装
- ユーザ空間
  - Nginx `stream` による TCP プロキシ
- `80` は `waf:80` に転送
- `443` は `waf:443` に転送
- 送信元 IP による制限はかけない
- 宛先ポートは `80` と `443` だけを公開する
- `80/443` 以外は listen しないため、FW1 のポリシーとしてはデフォルト拒否になる

## ポリシー整理

- L3 観点
  - 送信元は `Any`
  - 宛先はこの VM の公開先として `80/443` のみを対象にする
- L4 観点
  - TCP `80` を `waf:80` に転送
  - TCP `443` を `waf:443` に転送
- デフォルト動作
  - `80/443` 以外の宛先ポートは受けない
  - したがって、外周ポリシーとしては「宛先ホワイトリスト型、その他拒否」

## カーネル層の実装

FW1 をカーネル段階でも効かせたい場合は、同じディレクトリの `nftables` 補助スクリプトを使います。

- `apply-nft.sh`
  - host の `input` chain に `80/443` ホワイトリストを入れる
- `show-nft.sh`
  - 現在の FW1 table を表示する
- `remove-nft.sh`
  - FW1 table を削除する

デフォルトの許可ポート:

- `80`
- `443`

必要なら環境変数で上書きできます。

```bash
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
```

通常適用:

```bash
./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
```

削除:

```bash
./external-firewall/remove-nft.sh
```

## 今回の結果

- `nftables` を使った FW1 の companion 実装を追加した
- 追加したスクリプトは `apply-nft.sh`, `show-nft.sh`, `remove-nft.sh`
- これにより、`nginx stream` の前段で host の `input` chain に `80/443` ホワイトリストを入れられる
- 現時点ではスクリプト追加と Bash 構文確認まで完了している
- host への実適用と Mac からの到達確認は、まだこの README の手順を実行していない

## 注意

- これはクラウド WAF やネットワーク機器の完全代替ではなく、ローカル検証用の外周境界です
- 拒否時の見え方は環境依存で、`timeout` ではなく接続拒否に見える場合があります
- 純粋なカーネルパケットフィルタではなく、`nginx stream` による L4 境界です
- `apply-nft.sh` は host の `input` policy を `drop` にするため、`FW1_ALLOWED_TCP_PORTS` を `80,443` のまま適用すると `22` なども遮断します
- SSH を維持したい環境では `FW1_ALLOWED_TCP_PORTS="22,80,443"` のように管理ポートを明示的に含めてください

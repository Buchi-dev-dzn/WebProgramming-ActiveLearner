# External Firewall Test

このテストは、Mac ホストなど VM 外部から見たときに、公開入口が `80/443` に限定されているかを確認するためのものです。

## テスト対象

- `192.168.64.4:80`
- `192.168.64.4:443`
- その他の代表ポート

## スクリプト

- [portscan.py](/home/buchi/WebProgramming-ActiveLearner/other/test/external-firewall/portscan.py)

## 使い方

代表ポートを確認する:

```bash
python3 other/test/external-firewall/portscan.py 192.168.64.4 --ports 22,80,443,8080,5432
```

広めに確認する:

```bash
python3 other/test/external-firewall/portscan.py 192.168.64.4 --ports 1-1024
```

JSON で出す:

```bash
python3 other/test/external-firewall/portscan.py 192.168.64.4 --ports 22,80,443 --json
```

## 期待値

- `80`
  - `open`
- `443`
  - `open`
- それ以外
  - `closed` または `filtered`

## 有効時の意味

- `external-firewall` がホスト公開ポート `80/443` を受ける
- 後段は Docker 内部ネットワークへ閉じている

## 無効化方法

`external-firewall` を止める:

```bash
docker compose stop external-firewall
```

再開:

```bash
docker compose start external-firewall
```

## 無効時の見え方

- `80/443` も到達しなくなる
- 外部入口として `external-firewall` が本線上にあることを確認できる

## 比較ポイント

- 有効時
  - `80/443` が開いている
  - 他の代表ポートは閉じている
- 無効時
  - `80/443` も閉じる、または到達不能になる

## 補助テスト

host 側の `nftables` も比較したい場合:

```bash
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
./external-firewall/remove-nft.sh
```

詳細は [external-firewall/README.md](/home/buchi/WebProgramming-ActiveLearner/external-firewall/README.md) を参照。

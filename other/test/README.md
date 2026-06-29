# Security Layer Tests

このディレクトリには、Mac ホストなど Linux VM の外側から `External Firewall`, `NIPS`, `WAF` の動作を検証するためのテストを置きます。

## 目的

- `External Firewall`
  - 外部公開されているのが `80/443` だけか確認する
- `NIPS`
  - inline 防御としてレート異常を `429` で遮断するか確認する
- `WAF`
  - Web 向け攻撃パターンを `403/405` で遮断するか確認する

## 前提

- テストは Linux VM の中ではなく、Mac ホストなど VM 外部から実行する
- 想定 VM IP は `192.168.64.4`
- Python 3.10 以上を利用する
- HTTPS テストは自己署名証明書のため証明書検証を無効化している

## ディレクトリ

- [external-firewall/README.md](/home/buchi/WebProgramming-ActiveLearner/other/test/external-firewall/README.md)
  - ポート到達性の確認
- [nips/README.md](/home/buchi/WebProgramming-ActiveLearner/other/test/nips/README.md)
  - 正常疎通とレート遮断の確認
- [waf/README.md](/home/buchi/WebProgramming-ActiveLearner/other/test/waf/README.md)
  - 正常疎通と `403/405` 遮断の確認

## 使い分け

- 外部入口だけを見たい
  - `external-firewall/portscan.py`
- NIPS が「広く早く止める」か見たい
  - `nips/check_nips.py`
- WAF が Web 向けに「深く止める」か見たい
  - `waf/check_waf.py`

## 典型的な実行順序

1. External Firewall
   - `python3 other/test/external-firewall/portscan.py 192.168.64.4 --ports 22,80,443,8080,5432`
2. NIPS
   - `python3 other/test/nips/check_nips.py 192.168.64.4`
3. WAF
   - `python3 other/test/waf/check_waf.py 192.168.64.4`

## 注意

- `NIPS` のレート試験は短時間に多数のリクエストを送るため、VM が高負荷なタイミングでは結果がぶれることがある
- `WAF` の `403` と `405` は `WAF` 有効時を前提にしている
- `pass-through` 構成で比較したい場合は、各 README にある無効化・比較手順を使う

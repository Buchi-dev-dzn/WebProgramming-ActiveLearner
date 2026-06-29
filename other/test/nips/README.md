# NIPS Test

このテストは、Mac ホストなど VM 外部から `NIPS` の inline 防御を確認するためのものです。

## 何を検証するか

- 正常な通信は通るか
- 短時間の過剰リクエストに対して `429` を返すか

## スクリプト

- [check_nips.py](/home/buchi/WebProgramming-ActiveLearner/other/test/nips/check_nips.py)

## 使い方

基本実行:

```bash
python3 other/test/nips/check_nips.py 192.168.64.4
```

より強めにバーストさせる:

```bash
python3 other/test/nips/check_nips.py 192.168.64.4 --burst-size 180 --concurrency 80
```

JSON で出す:

```bash
python3 other/test/nips/check_nips.py 192.168.64.4 --json
```

## 期待値

- baseline request
  - `200`
- burst request
  - 少なくとも一部が `429`

## 有効時の意味

- `NIPS` が送信元単位の接続数やリクエストレートを監視している
- 異常なバーストを `429` で遮断する

## 無効化方法

`NIPS` 自体を止める:

```bash
docker compose stop nips
```

再開:

```bash
docker compose start nips
```

## 検査だけを無効化する方法

本線を維持したまま `NIPS` の判定だけ外す:

```bash
docker compose -f docker-compose.yml -f docker-compose.nips-bypass.yml up -d --force-recreate nips
```

通常の `NIPS` に戻す:

```bash
docker compose up -d --force-recreate nips
```

## 比較ポイント

- 通常 NIPS
  - 正常通信は通る
  - 過剰バーストは `429`
- pass-through NIPS
  - 正常通信は通る
  - NIPS 自体では `429` を返さなくなる
- stopped NIPS
  - 本線が切れ、正常通信も成立しない

## 補足

このテストは `NIPS` に特有なレート制御確認を主目的としているため、`sqlmap` や `XSS` のような Web 攻撃シグネチャ確認は `WAF` テスト側に分離している。

詳細は [nips/README.md](/home/buchi/WebProgramming-ActiveLearner/nips/README.md) を参照。

# WAF Test

このテストは、Mac ホストなど VM 外部から `WAF` の Web 向け遮断を確認するためのものです。

## 何を検証するか

- 正常な HTTP/HTTPS は通るか
- 危険な User-Agent を `403` で止めるか
- 危険な query を `403` で止めるか
- 非許可メソッドを `405` で止めるか

## スクリプト

- [check_waf.py](/home/buchi/WebProgramming-ActiveLearner/other/test/waf/check_waf.py)

## 使い方

基本実行:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4
```

JSON で出す:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4 --json
```

## 期待値

- `GET /`
  - `200`
- `GET /api/health` over HTTPS
  - `200`
- `User-Agent: sqlmap`
  - `403`
- `?q=<script>`
  - `403`
- `?q=union%20select`
  - `403`
- `PUT /`
  - `405`

## 有効時の意味

- `WAF` が後段の `reverse-proxy` の前で HTTP/HTTPS を精査している
- Web 向け攻撃パターンや非許可メソッドを遮断している

## 無効化方法

`WAF` 自体を止める:

```bash
docker compose stop waf
```

再開:

```bash
docker compose start waf
```

## 検査だけを無効化する方法

本線を維持したまま `WAF` の判定だけ外す:

```bash
docker compose -f docker-compose.yml -f docker-compose.waf-bypass.yml up -d --force-recreate waf
```

通常の `WAF` に戻す:

```bash
docker compose up -d --force-recreate waf
```

## 比較ポイント

- 通常 WAF
  - 正常通信は通る
  - 危険 UA / 危険 query は `403`
  - 非許可メソッドは `405`
- pass-through WAF
  - 正常通信は通る
  - WAF 自体では `403/405` を返さなくなる
- stopped WAF
  - 本線が切れ、正常通信も成立しない

詳細は [waf/README.md](/home/buchi/WebProgramming-ActiveLearner/waf/README.md) を参照。

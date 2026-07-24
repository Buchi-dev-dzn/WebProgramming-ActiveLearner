# WAF Test

> 実装状況: 2026-07-24 時点。通常設定と `docker-compose.waf-bypass.yml` を使う pass-through 設定を比較します。外部公開は HTTPS (`443`) のみです。

このテストは、Mac ホストなど VM 外部から `WAF` の Web 向け遮断を確認するためのものです。

## 前提

- テストは Linux VM の中ではなく、Mac ホストなど VM 外部から実行する
- 想定 VM IP は `192.168.64.4`
- Python 3.10 以上を利用する
- HTTPS は自己署名証明書のため、スクリプト内で証明書検証を無効化している
- 通常構成では `external-firewall -> nips -> waf -> reverse-proxy -> internal-firewall -> fastapi-app` の本線が起動している前提

## 何を検証するか

- 正常な HTTP/HTTPS は通るか
- 危険な User-Agent を `403` で止めるか
- 危険な query を `403` で止めるか
- 危険な path や URL override header を `403` で止めるか
- 未許可ルートを `404` で閉じるか
- 非許可メソッドを `405` で止めるか
- 認証・出品者プロフィールの許可ルートだけを後段に通すか
- `/api/auth/debug` や `/api/seller/internal` のような未知ルートを `404` で閉じるか

## スクリプト

- [check_waf.py](/home/buchi/WebProgramming-ActiveLearner/other/test/waf/check_waf.py)

## 使い方

基本実行:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4
```

タイムアウトを長めにする:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4 --timeout 5
```

JSON で出す:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4 --json
```

## 期待値

- `GET /`
  - `404`
- `GET /api/health` over HTTPS
  - `200`
- `User-Agent: sqlmap`
  - `403`
- `?q=<script>`
  - `403`
- `?q=union%20select`
  - `403`
- `GET /.git/config`
  - `403`
- `X-Original-URL: /admin`
  - `403`
- `GET /admin`
  - `404`
- `PUT /`
  - `405`
- `POST /api/auth/register` with empty JSON
  - `422`
  - WAF は許可し、後段 FastAPI の validation error が返る
- `GET /api/auth/debug`
  - `404`
- `GET /api/seller/profile` without token
  - `401`
  - WAF は許可し、後段 FastAPI の認証エラーが返る
- `GET /api/seller/internal`
  - `404`

## 有効時の意味

- `WAF` が後段の `reverse-proxy` の前で HTTP/HTTPS を精査している
- Web 向け攻撃パターン、URL override header、不要ルート探索を遮断している
- 到達可能なルートが必要最小限に絞られている

## 出力の見方

通常実行では 1 ケース 1 行で結果が出る。

- `expected`
  - 期待するステータスコード
- `actual`
  - 実際に返ったステータスコード
- `matched=yes`
  - 期待どおり
- `matched=no`
  - 期待と違うため、設定漏れか経路異常を疑う

末尾の `all_matched yes` が、全ケース成功の目印。

正常系の例:

```text
http_root_ok expected=404 actual=404 matched=yes {"error":"not_found"}
https_api_health_ok expected=200 actual=200 matched=yes {"service":"fastapi-api","status":"ok","checks":{"postgres":{"status":"ok"}},"request_id":"..."}
```

遮断系の例:

```text
blocked_sqlmap_ua expected=403 actual=403 matched=yes <html>...
blocked_dotgit_path expected=403 actual=403 matched=yes <html>...
unknown_route_not_found expected=404 actual=404 matched=yes <html>...
blocked_put_method expected=405 actual=405 matched=yes <html>...
all_matched yes
```

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

## 比較のしかた

まず通常 WAF で確認する:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4
```

次に pass-through で比較する:

```bash
docker compose -f docker-compose.yml -f docker-compose.waf-bypass.yml up -d --force-recreate waf
python3 other/test/waf/check_waf.py 192.168.64.4
```

最後に通常 WAF に戻す:

```bash
docker compose up -d --force-recreate waf
```

## 比較ポイント

- 通常 WAF
  - 正常通信は通る
  - 危険 UA / 危険 query / 危険 path / URL override header は `403`
  - 未許可ルートは `404`
  - 非許可メソッドは `405`
- pass-through WAF
  - 正常通信は通る
  - WAF 自体では `403/404/405` を返さなくなる
- stopped WAF
  - 本線が切れ、正常通信も成立しない

## 異常時の見方

- 正常系まで失敗する
  - `waf` 停止、後段停止、または本線断を疑う
- `403` になるべきケースが `200`
  - WAF ルールが弱い、または pass-through 構成が有効な可能性がある
- `404` になるべきケースが `200`
  - 許可ルート制限が効いていない
- `405` になるべきケースが `200`
  - メソッド制限が効いていない

詳細は [waf/README.md](/home/buchi/WebProgramming-ActiveLearner/waf/README.md) を参照。

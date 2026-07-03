# Reverse Proxy Test

このテストは、Mac ホストなど VM 外部から `reverse-proxy` の DMZ 公開中継点としての振る舞いを確認するためのものです。

## テスト対象

- `/health` が `reverse-proxy` のヘルス応答を返すか
- `/` が `404` で閉じるか
- `/api` が現行の公開面では `404` で閉じるか
- `/api/info` が後段 FastAPI のレスポンスを代理返却するか
- `X-Request-Id` がレスポンス本文とヘッダで一致するか
- 後段停止時に `503 {"error":"upstream_unavailable"}` を返すか

## スクリプト

- [check_reverse_proxy.py](/home/buchi/WebProgramming-ActiveLearner/other/test/reverse-proxy/check_reverse_proxy.py)

## 使い方

通常確認:

```bash
python3 other/test/reverse-proxy/check_reverse_proxy.py 192.168.64.4
```

JSON で出す:

```bash
python3 other/test/reverse-proxy/check_reverse_proxy.py 192.168.64.4 --json
```

後段停止比較:

```bash
docker compose stop internal-firewall
python3 other/test/reverse-proxy/check_reverse_proxy.py 192.168.64.4 --expect-upstream-unavailable
docker compose start internal-firewall
```

## 期待値

通常時:

- `GET /health`
  - `200`
  - body は `reverse-proxy ok`
- `GET /`
  - `404`
  - body は `{"error":"not_found"}`
- `GET /api`
  - `404`
  - WAF の許可ルート制限により公開面では閉じる
- `GET /api/unknown`
  - `404`
  - body は `{"error":"not_found", ...}`
- `GET /api/info`
  - `200`
  - `service=fastapi-api`
  - `via=["reverse-proxy","internal-firewall","fastapi-api"]`
  - `X-Request-Id` が本文の `request_id` と一致

後段停止時:

- `GET /api/info`
  - `502` または `503`
  - body は `{"error":"upstream_unavailable"}`

## 比較ポイント

- 通常時
  - RP は公開許可された `/api/info` を後段へ通し、レスポンスを代理返却する
- internal-firewall 停止時
  - RP 自身が `503` を返し、後段障害を外向きに正規化する
- reverse-proxy 停止時
  - 本線が切れ、正常通信も成立しない

## 異常時の見方

- `/health` が `200` でない
  - RP 停止、WAF 側経路異常、または本線断を疑う
- `/api/info` が `200` だが `request_id` が無い
  - RP のヘッダ伝播設定漏れを疑う
- 後段停止時に `503` 以外
  - RP 側の upstream error 制御漏れを疑う

# Security Layer Tests

このディレクトリには、Mac ホストなど Linux VM の外側から `External Firewall`, `NIPS`, `WAF`, `Reverse Proxy`, `FastAPI` の動作を検証するためのテストを置きます。

## 目的

- `External Firewall`
  - 外部公開されているのが `80/443` だけか確認する
- `NIPS`
  - inline 防御としてレート異常を `429` で遮断するか確認する
- `WAF`
  - Web 向け攻撃パターン、不要ルート探索、非許可メソッドを `403/404/405` で遮断するか確認する
- `Reverse Proxy`
  - DMZ 公開中継点として `/health`, ルート制限, request ID, upstream 障害応答を制御できるか確認する
- `FastAPI`
  - `/api/health`, `/api/info` と依存 DB 劣化時の JSON 応答を確認する
- `Auth Crypto`
  - ユーザー登録、ログイン、JWT、出品者プロフィール、DB 内の平文混入有無を確認する

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
  - 正常疎通と `403/404/405` 遮断の確認
- [reverse-proxy/README.md](/home/buchi/WebProgramming-ActiveLearner/other/test/reverse-proxy/README.md)
  - DMZ 中継と upstream 障害時の確認
- [fastapi/README.md](/home/buchi/WebProgramming-ActiveLearner/other/test/fastapi/README.md)
  - API 応答と DB 劣化時の確認
- [auth-crypto/README.md](/home/buchi/WebProgramming-ActiveLearner/other/test/auth-crypto/README.md)
  - 認証・暗号化・出品者プロフィールの確認

## 使い分け

- 外部入口だけを見たい
  - `external-firewall/portscan.py`
- NIPS が「広く早く止める」か見たい
  - `nips/check_nips.py`
- WAF が Web 向けに「深く止める」か見たい
  - `waf/check_waf.py`
- RP が DMZ 公開中継点として正しく返すか見たい
  - `reverse-proxy/check_reverse_proxy.py`
- FastAPI が正常系と劣化系でどう返すか見たい
  - `fastapi/check_fastapi.py`
- 認証情報や個人情報の保存方針を見たい
  - `auth-crypto/check_auth_crypto.py`

## 典型的な実行順序

1. External Firewall
   - `python3 other/test/external-firewall/portscan.py 192.168.64.4 --ports 22,80,443,8080,5432`
2. NIPS
   - `python3 other/test/nips/check_nips.py 192.168.64.4`
3. WAF
   - `python3 other/test/waf/check_waf.py 192.168.64.4`
4. Reverse Proxy
   - `python3 other/test/reverse-proxy/check_reverse_proxy.py 192.168.64.4`
5. FastAPI
   - `python3 other/test/fastapi/check_fastapi.py 192.168.64.4`
6. Auth Crypto
   - `python3 other/test/auth-crypto/check_auth_crypto.py 192.168.64.4`
   - `python3 other/test/auth-crypto/check_auth_crypto.py 192.168.64.4 --check-db --compose-dir /home/buchi/WebProgramming-ActiveLearner`

## 注意

- `NIPS` のレート試験は短時間に多数のリクエストを送るため、VM が高負荷なタイミングでは結果がぶれることがある
- `WAF` の `403/404/405` は `WAF` 有効時を前提にしている
- `Reverse Proxy` の比較では `internal-firewall` 停止時の `503` を見る
- `FastAPI` の比較では `postgres` 停止時の `503/degraded` を見る
- `Auth Crypto` の DB 内検証は Docker API にアクセスできる環境でのみ実行する
- `pass-through` 構成で比較したい場合は、各 README にある無効化・比較手順を使う

## 比較メモ用テーブル

この節は、`有効時`, `停止時`, `pass-through 時` の結果を横並びで記録するためのメモ欄です。  
報告書では、この表を埋めると「その層があるとき / ないとき / 検査だけ外したとき」の差を説明しやすくなります。

### External Firewall

| 項目 | 有効時 | 停止時 | メモ |
| --- | --- | --- | --- |
| `80/tcp` | open | filtered |  |
| `443/tcp` | open | filtered |  |
| `22/tcp` | open | open |  |
| `8080/tcp` | filtered | filtered |  |
| `5432/tcp` | filtered | filtered |  |
| 外部入口としての意味 | `80/443` が唯一の入口 | 入口自体が失われる |  |

推奨コマンド:

```bash
python3 other/test/external-firewall/portscan.py 192.168.64.4 --ports 22,80,443,8080,5432
```

### NIPS

#### 検知無効時
baseline_http 200 reverse-proxy ok
baseline_https 200 {"service":"fastapi-api","status":"ok","checks":{"postgres":{"status":"ok"}},"request_id":"..."}
burst total=180 ok_200=180 blocked_429=0 other={}
nips_effect_detected no

#### 検知有効時
> python3 test/check_nips.py 192.168.64.4 --burst-size 180 --concurrency 80
baseline_http 200 reverse-proxy ok
baseline_https 200 {"service":"fastapi-api","status":"ok","checks":{"postgres":{"status":"ok"}},"request_id":"..."}
burst total=180 ok_200=59 blocked_429=121 other={}
nips_effect_detected yes

推奨コマンド:

```bash
python3 other/test/nips/check_nips.py 192.168.64.4
```

整理の観点:

- `通常 NIPS`
  - 正常通信は通る
  - burst 時に `429` が出る
- `pass-through NIPS`
  - 正常通信は通る
  - NIPS 自体では `429` が出なくなる
- `stopped NIPS`
  - 本線が切れ、正常通信も成立しない

### WAF

#### 検知無効時
~/Documents/Programming/school/web_programming/test | [0s]
> python3 check_waf.py 192.168.64.4
http_root_ok expected=404 actual=404 matched=yes {"error":"not_found"}
https_api_health_ok expected=200 actual=200 matched=yes {"service":"fastapi-api","status":"ok","checks":{"postgres":{"status":"ok"}},"request_id":"..."}
blocked_sqlmap_ua expected=403 actual=404 matched=no {"error":"not_found"}
blocked_script_query expected=403 actual=404 matched=no {"error":"not_found"}
blocked_union_query expected=403 actual=404 matched=no {"error":"not_found"}
blocked_dotgit_path expected=403 actual=404 matched=no {"error":"not_found"}
blocked_override_header expected=403 actual=200 matched=no {"service":"fastapi-api","status":"ok","checks":{"postgres":{"status":"ok"}},"request_id":"..."}
unknown_route_not_found expected=404 actual=404 matched=yes <html>...
blocked_put_method expected=405 actual=405 matched=yes <html>...
all_matched no

#### 検知有効時

~/Documents/Programming/school/web_programming/test | [0s]
> python3 check_waf.py 192.168.64.4
http_root_ok expected=404 actual=404 matched=yes {"error":"not_found"}
https_api_health_ok expected=200 actual=200 matched=yes {"service":"fastapi-api","status":"ok","checks":{"postgres":{"status":"ok"}},"request_id":"..."}
blocked_sqlmap_ua expected=403 actual=403 matched=yes <html>
<head><title>403 Forbidden</title></head>
<body>
<center><h1>403 Forbidden</h1></center>
<hr><center>nginx</center>
</body>
</html>
blocked_script_query expected=403 actual=403 matched=yes <html>
<head><title>403 Forbidden</title></head>
<body>
<center><h1>403 Forbidden</h1></center>
<hr><center>nginx</center>
</body>
</html>
blocked_union_query expected=403 actual=403 matched=yes <html>
<head><title>403 Forbidden</title></head>
<body>
<center><h1>403 Forbidden</h1></center>
<hr><center>nginx</center>
</body>
</html>
blocked_dotgit_path expected=403 actual=404 matched=no <html>
<head><title>404 Not Found</title></head>
<body>
<center><h1>404 Not Found</h1></center>
<hr><center>nginx</center>
</body>
</html>
blocked_override_header expected=403 actual=403 matched=yes <html>
<head><title>403 Forbidden</title></head>
<body>
<center><h1>403 Forbidden</h1></center>
<hr><center>nginx</center>
</body>
</html>
unknown_route_not_found expected=404 actual=404 matched=yes <html>
<head><title>404 Not Found</title></head>
<body>
<center><h1>404 Not Found</h1></center>
<hr><center>nginx</center>
</body>
</html>
blocked_put_method expected=405 actual=405 matched=yes <html>
<head><title>405 Not Allowed</title></head>
<body>
<center><h1>405 Not Allowed</h1></center>
<hr><center>nginx</center>
</body>
</html>
all_matched no

~/Documents/Programming/school/web_programming/test | [0s]
> 

推奨コマンド:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4
```

整理の観点:

- `通常 WAF`
  - 正常通信は通る
  - 危険 UA / 危険 query / 危険 path / URL override header は `403`
  - 未許可ルートは `404`
  - 非許可メソッドは `405`
- `pass-through WAF`
  - 正常通信は通る
  - WAF 自体では `403/404/405` を返さなくなる
- `stopped WAF`
  - 本線が切れ、正常通信も成立しない

### Reverse Proxy

| 項目 | 通常時 | 後段停止時 | メモ |
| --- | --- | --- | --- |
| `GET /health` | `200 reverse-proxy ok` | `200 reverse-proxy ok` | RP 自身の生存確認 |
| `GET /` | `404 {"error":"not_found"}` | `404 {"error":"not_found"}` | 公開面を広げない |
| `GET /api` | `404` | `404` | WAF の許可ルート外 |
| `GET /api/info` | `200 fastapi-api` | `503 upstream_unavailable` | RP の代理返却と障害正規化 |
| `request_id` | ヘッダと本文で一致 | なしでも可 | 正常時の伝播確認 |

推奨コマンド:

```bash
python3 other/test/reverse-proxy/check_reverse_proxy.py 192.168.64.4
docker compose stop internal-firewall
python3 other/test/reverse-proxy/check_reverse_proxy.py 192.168.64.4 --expect-upstream-unavailable
docker compose start internal-firewall
```

### FastAPI

| 項目 | 通常時 | DB停止時 | メモ |
| --- | --- | --- | --- |
| `GET /api/health` | `200 status=ok` | `503 status=degraded` | 依存障害の見え方 |
| `GET /api/info` | `200 fastapi-api` | `200 fastapi-api` | アプリ自体の生存 |
| `request_id` | ヘッダと本文で一致 | ヘッダと本文で一致 | 追跡性 |

推奨コマンド:

```bash
python3 other/test/fastapi/check_fastapi.py 192.168.64.4
docker compose stop postgres
python3 other/test/fastapi/check_fastapi.py 192.168.64.4 --expect-degraded-health
docker compose start postgres
```

### Auth Crypto

2026-07-10 に Windows 側クライアントから Linux VM の公開入口 `172.16.30.197` に対して、認証・暗号化基盤の API 検証を実施しました。

実行コマンド:

```powershell
python check_auth_crypto.py 172.16.30.197
```

確認できたこと:

- `POST /api/auth/register`
  - seller ユーザーを登録できた
  - `role=seller`, `is_active=true` を返した
- `POST /api/auth/register` の重複 email
  - `409 {"detail":{"error":"email_already_registered"}}` を返した
  - email の大文字小文字差分を正規化して同一扱いできている
- `POST /api/auth/login` の誤 password
  - `401 {"detail":{"error":"invalid_credentials"}}` を返した
- `POST /api/auth/login` の正しい password
  - `200`
  - JWT 形式の `access_token` を返した
  - `token_type=bearer`
  - `expires_in=900`
- `GET /api/auth/me`
  - Bearer token 付きで `200`
  - 登録済みユーザー情報を返した
- `GET /api/auth/me` token なし
  - `401 {"detail":{"error":"missing_token"}}` を返した
- `POST /api/seller/profile`
  - 出品者プロフィールを作成できた
  - `business_email`, `phone`, `business_address` を API 上で復号済みレスポンスとして返した
- `GET /api/seller/profile`
  - 作成済み出品者プロフィールを取得できた

結果:

```text
register_seller              matched=yes
duplicate_email_rejected     matched=yes
bad_password_rejected        matched=yes
login_issues_jwt             matched=yes
auth_me_accepts_bearer_token matched=yes
auth_me_requires_token       matched=yes
seller_profile_upsert        matched=yes
seller_profile_get           matched=yes
db_plaintext_inspection      skipped
all_matched                  yes
```

この結果から、外部公開入口から次の本線を通って認証・出品者プロフィール API が成立していることを確認できました。

```text
Windows client
  -> 172.16.30.197
  -> external-firewall
  -> nips
  -> waf
  -> reverse-proxy
  -> internal-firewall
  -> fastapi-app
  -> postgres
```

残っている確認:

- `db_plaintext_inspection` は未実行
- DB 内に平文 email / password が保存されていないことは、Linux VM 側で `--check-db` を付けて確認する

推奨コマンド:

```bash
cd /home/buchi/WebProgramming-ActiveLearner
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1 --check-db --compose-dir /home/buchi/WebProgramming-ActiveLearner
```

期待値:

```text
db_plaintext_inspection matched=yes
all_matched yes
```

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
baseline_http 200 waf ok
baseline_https 200 {"service":"backend-api","status":"ok","checks":{"postgres":{"status":"ok"}}}
burst total=180 ok_200=180 blocked_429=0 other={}
nips_effect_detected no

#### 検知有効時
> python3 test/check_nips.py 192.168.64.4 --burst-size 180 --concurrency 80
baseline_http 200 waf ok
baseline_https 200 {"service":"backend-api","status":"ok","checks":{"postgres":{"status":"ok"}}}
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
> python3 test/check_waf.py 192.168.64.4                                   
http_root_ok expected=200 actual=200 matched=yes reverse-proxy active
https_api_health_ok expected=200 actual=200 matched=yes {"service":"backend-api","status":"ok","checks":{"postgres":{"status":"ok"}}}
blocked_sqlmap_ua expected=403 actual=200 matched=no reverse-proxy active
blocked_script_query expected=403 actual=200 matched=no reverse-proxy active
blocked_union_query expected=403 actual=200 matched=no reverse-proxy active
blocked_put_method expected=405 actual=200 matched=no reverse-proxy active
all_matched > [!NOTE]
> 

#### 検知有効時
[.venv] ~/Documents/Programming/school/web_programming | [0s]
> python3 test/check_waf.py 192.168.64.4
http_root_ok expected=200 actual=200 matched=yes reverse-proxy active
https_api_health_ok expected=200 actual=200 matched=yes {"service":"backend-api","status":"ok","checks":{"postgres":{"status":"ok"}}}
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
blocked_put_method expected=405 actual=405 matched=yes <html>
<head><title>405 Not Allowed</title></head>
<body>
<center><h1>405 Not Allowed</h1></center>
<hr><center>nginx</center>
</body>
</html>
all_matched yes




推奨コマンド:

```bash
python3 other/test/waf/check_waf.py 192.168.64.4
```

整理の観点:

- `通常 WAF`
  - 正常通信は通る
  - 危険 UA / 危険 query は `403`
  - 非許可メソッドは `405`
- `pass-through WAF`
  - 正常通信は通る
  - WAF 自体では `403/405` を返さなくなる
- `stopped WAF`
  - 本線が切れ、正常通信も成立しない

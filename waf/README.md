# WAF Layer

このディレクトリには、`external-firewall` の後段に置く `waf` コンテナの設定を置いています。

## 役割

- `external-firewall` から渡された HTTP/HTTPS を受ける
- 単純な不正リクエストやスキャンを早い段階で落とす
- 正常な通信だけを `reverse-proxy` に渡す

## 現在の内容

- `nginx.conf`
  - WAF レイヤー全体の nginx 設定
- `conf.d/default.conf`
  - 簡易ルール
  - 許可 HTTP メソッド制限
  - 危険な User-Agent の拒否
  - 明らかな path traversal や SQLi/XSS を狙う文字列の拒否
- `certs/dev.crt`, `certs/dev.key`
  - Step 1 の HTTPS 疎通確認用の自己署名証明書

## Step 1 での HTTPS の扱い

- `external-firewall` から受けた `80` と `443` を WAF で処理する
- TLS 終端は WAF で行う
- 証明書はローカル検証用の自己署名であり、本番用途ではない
- `curl -k https://<host>/api/health` のように疎通確認する前提

## 注意

これは `ModSecurity + OWASP CRS` の本格 WAF ではなく、その前段の簡易ガードです。
学習用・構成確認用としては十分ですが、本番では dedicated WAF ルールセットへ置き換える前提です。

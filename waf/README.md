# WAF Layer

このディレクトリには、外部公開の最前段に置く `waf` コンテナの設定を置いています。

## 役割

- Client から入る HTTP/HTTPS の最初の受け口になる
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

## 注意

これは `ModSecurity + OWASP CRS` の本格 WAF ではなく、その前段の簡易ガードです。
学習用・構成確認用としては十分ですが、本番では dedicated WAF ルールセットへ置き換える前提です。

# Nginx Reverse Proxy

このディレクトリには、Linux VM 側の外部入口となる nginx 設定を置いています。

## 目的

- WAF の後段で受けた HTTP リクエストを backend へ振り分ける
- `/api/...` を backend に転送する
- 後から WAF や TLS 設定を差し込みやすくする

## ファイル

- `nginx.conf`
  - nginx 全体設定
  - ログ形式、`conf.d` 読み込みなど
- `conf.d/default.conf`
  - default server 設定
  - `/health`, `/api/`, `/` のルーティング

## 現状

- 外部公開はしていない
- `waf` からの内部通信を受ける
- `443` は WAF 側で公開しているが、まだ TLS 設定は未投入

## 次の拡張

- TLS 証明書設定
- ModSecurity + OWASP CRS
- rate limit
- security header

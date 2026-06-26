# Nginx Reverse Proxy

このディレクトリには、DMZ に置く `reverse-proxy` コンテナの nginx 設定を置いています。

## 目的

- 公開用の入口として HTTP/HTTPS を受ける
- `/api/...` を `application` コンテナへ転送する
- DMZ と内部アプリ層の境界を分けやすくする

## ファイル

- `nginx.conf`
  - nginx 全体設定
  - ログ形式、`conf.d` 読み込みなど
- `conf.d/default.conf`
  - default server 設定
  - `/health`, `/api/`, `/` のルーティング

## 現状

- `external-firewall` から渡された `80/443` を受ける
- host には直接公開しない
- `application` コンテナだけに中継する
- 自己署名証明書で `443` を終端する

## 次の拡張

- WAF の再導入
- rate limit
- upstream の多重化

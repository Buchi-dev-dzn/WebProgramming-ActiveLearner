# Logs Directory

このディレクトリは、各コンテナのログ保存先です。

## サブディレクトリ

- `external-firewall/`
  - external firewall のアクセスログとエラーログ
- `nginx/`
  - reverse proxy のアクセスログとエラーログ
- `application/`
  - internal firewall と backend API のログ
- `postgres/`
  - PostgreSQL 関連ログの保存先として確保

## 補足

- `waf/`, `backend/` は旧構成のログ置き場として残しています
- 現在の構成で主に使うのは `external-firewall/`, `nginx/`, `application/`, `postgres/` です

## 目的

- コンテナ再作成時にログをホスト側に残す
- 後からログ集約基盤へ取り込めるようにする
- レポートで「ログをどこに残しているか」を示しやすくする

## 補足

本格的な可視化は、後で `Loki/Grafana` や `ELK` へ接続する想定です。

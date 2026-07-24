# Logs Directory

> 実装状況: 2026-07-24 時点。ログファイルは実行時データであり、文書更新では内容を変更しません。

このディレクトリは、各コンテナのログ保存先です。

## サブディレクトリ

- `external-firewall/`
  - external firewall のアクセスログとエラーログ
- `waf/`
  - WAF のアクセスログとエラーログ
- `nginx/`
  - reverse proxy のアクセスログとエラーログ
- `internal-firewall/`
  - internal firewall のアクセスログとエラーログ
- `postgres/`
  - PostgreSQL 関連ログの保存先として確保
- `nids/`
  - NIDS センサーの検知結果
- `hids/`
  - HIDS/HIPS センサーの検知結果

## 旧ディレクトリ

- `application/`, `backend/` は旧構成のログ置き場として残しています
- 現在の構成で主に使うのは `external-firewall/`, `waf/`, `nginx/`, `internal-firewall/`, `postgres/`, `nids/`, `hids/` です

## 目的

- コンテナ再作成時にログをホスト側に残す
- 後からログ集約基盤へ取り込めるようにする
- レポートで「ログをどこに残しているか」を示しやすくする

## 将来候補

本格的な可視化は、後で `Loki/Grafana` や `ELK` へ接続する想定です。

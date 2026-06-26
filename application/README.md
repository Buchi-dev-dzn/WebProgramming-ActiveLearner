# Application Layer

このディレクトリには、Application 層コンテナの設定を置いています。

## 役割

- `reverse-proxy` からだけ到達する内部アプリ層を表現する
- コンテナ内の nginx を Internal Firewall として使い、`/api/` だけを backend に通す
- backend API から PostgreSQL の状態を返す

## ファイル

- `Dockerfile`
  - nginx と Node.js を同居させる実行イメージ
- `nginx.conf`
  - Internal Firewall の全体設定
- `conf.d/default.conf`
  - `/api/` のみ backend に通す内部向けルーティング
- `start.sh`
  - backend と nginx を起動するエントリポイント

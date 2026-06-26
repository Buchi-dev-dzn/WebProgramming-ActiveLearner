# Backend

このディレクトリには、Application 層コンテナ内で動く疎通確認用 backend 実装を置いています。

## 目的

- reverse proxy と internal firewall を通して backend へ流せることを確認する
- backend を外部公開せず、内部ネットワークでのみ到達させる
- PostgreSQL への接続状態を API から確認できるようにする
- ログ出力先の形を先に決める

## ファイル

- `server.js`
  - Node.js 標準 `http` モジュールを使った最小 API
  - `pg` を使った依存先ヘルスチェックを含む
- `package.json`
  - backend の依存定義
- `Dockerfile`
  - Compose から build するための実行イメージ定義

## 現在のエンドポイント

- `GET /health`
  - backend 直通のヘルスチェック
  - PostgreSQL の状態を JSON で返す
- `GET /api/health`
  - reverse proxy / WAF 経由で到達確認しやすい集約ヘルスチェック
- `GET /api/info`
  - ベース構成の説明レスポンス

## 補足

この実装は最小構成です。後で Express, Fastify, NestJS などの本実装に差し替える想定です。

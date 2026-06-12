# Backend Placeholder

このディレクトリには、疎通確認用の最小 backend 実装を置いています。

## 目的

- reverse proxy から backend へ流せることを確認する
- backend を外部公開せず、内部ネットワークでのみ到達させる
- ログ出力先の形を先に決める

## ファイル

- `server.js`
  - Node.js 標準 `http` モジュールを使った最小 API

## 現在のエンドポイント

- `GET /health`
  - backend のヘルスチェック
- `GET /api/info`
  - ベース構成の説明レスポンス

## 補足

この実装は仮置きです。後で Express, Fastify, NestJS などの本実装に差し替える想定です。

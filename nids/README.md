# NIDS

`NIDS` は `Network Intrusion Detection System` の略で、通信本線を止めるのではなく、横から観測して異常を検知する層です。

今回のリポジトリでは、独立したパケットミラー型センサーまではまだ置かず、まず `audit_events` を使ってアプリケーション入口で観測できる認証異常を検知材料にします。

## 現在の実装

- `NIPS`
  - `nips/` の HAProxy が inline で遮断する
- `NIDS`
  - `audit_events` の warning 系イベントを集計する
  - `/api/security/monitoring/summary` で直近 24 時間の異常シグナルを見る

## 見るイベント

- `auth_login_failed`
  - password 誤りや存在しない email へのログイン試行
- `auth_login_blocked`
  - ログイン失敗回数超過による一時ロック
- `auth_refresh_failed`
  - 失効済み、期限切れ、不正な refresh token の利用

## 実運用での発展形

本番寄りにする場合は、次を追加します。

- switch / host / cloud load balancer からの traffic mirror
- Zeek, Suricata などの NIDS
- NIPS / WAF / reverse proxy / FastAPI のログ集約
- `request_id` による通信ログと `audit_events` の相関

# NIDS

`NIDS` は `Network Intrusion Detection System` の略で、通信本線を止めるのではなく、横から観測して異常を検知する層です。

今回のリポジトリでは、実 NIC のパケットミラーまでは再現せず、Docker 上のログ監視センサーとして `nids` コンテナを追加します。通信本線は止めず、external firewall / WAF / reverse proxy / internal firewall のログを横から読み、検知結果を `logs/nids/alerts.log` に残します。

## 現在の実装

- `NIPS`
  - `nips/` の HAProxy が inline で遮断する
- `NIDS`
  - `nids` コンテナが network boundary のログを横から読む
  - 疑わしい HTTP status、攻撃文字列、scanner User-Agent などを検知する
  - 検知結果を `logs/nids/alerts.log` に JSON Lines で保存する
  - `audit_events` の warning 系イベントも集計する
  - `/api/security/monitoring/summary` で直近 24 時間の異常シグナルを見る

## 見るイベント

- `logs/external-firewall/*.log`
  - 外部入口の access / error log
- `logs/waf/*.log`
  - WAF の遮断結果
- `logs/nginx/*.log`
  - reverse proxy の access / error log
- `logs/internal-firewall/*.log`
  - 内部境界の access / error log
- `auth_login_failed`
  - password 誤りや存在しない email へのログイン試行
- `auth_login_blocked`
  - ログイン失敗回数超過による一時ロック
- `auth_refresh_failed`
  - 失効済み、期限切れ、不正な refresh token の利用

## Docker 上の配置

`nids` は通信本線には入れません。`edge_net` と `app_net` には接続しますが、`ports` は持たせず外部公開しません。

```text
Client -> external-firewall -> nips -> waf -> reverse-proxy -> internal-firewall -> fastapi-app
                         \
                          nids reads boundary logs
```

この構成は、実運用の SPAN / traffic mirror を完全再現するものではありません。学習用環境では「本線を止めない監視専用 IDS」という責務を、ログ監視として再現しています。

## 実運用での発展形

本番寄りにする場合は、次を追加します。

- switch / host / cloud load balancer からの traffic mirror
- Zeek, Suricata などの NIDS
- NIPS / WAF / reverse proxy / FastAPI のログ集約
- `request_id` による通信ログと `audit_events` の相関

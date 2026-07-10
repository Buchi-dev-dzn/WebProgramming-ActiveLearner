# HIDS / HIPS

`HIDS` は `Host Intrusion Detection System`、`HIPS` は `Host Intrusion Prevention System` の略です。

今回の構成では、`fastapi-app` をアプリケーションホスト相当として扱い、ホスト内部で見える認証状態や token 状態を `audit_events` に残します。

## 現在の実装

- ログイン失敗回数を `users.failed_login_count` に保存する
- 一定回数を超えたユーザーを `users.locked_until` で一時ロックする
- refresh token を `refresh_tokens` に HMAC hash として保存する
- refresh token のローテーションと logout 時の失効を記録する
- 本人用 `/api/auth/audit-events` と管理者用 `/api/security/audit-events` で監査イベントを確認する

## HIDS/HIPS として見るシグナル

- アカウントロック
- refresh token の再利用拒否
- logout による refresh token 失効
- 権限付き監査 API へのアクセス

## 実運用での発展形

本番寄りにする場合は、次を追加します。

- ファイル改ざん検知
- プロセス監視
- OS 認証ログの収集
- コンテナ runtime 監視
- Wazuh, osquery, auditd などとの連携

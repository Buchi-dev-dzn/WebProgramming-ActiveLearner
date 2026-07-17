# HIDS / HIPS

`HIDS` は `Host Intrusion Detection System`、`HIPS` は `Host Intrusion Prevention System` の略です。

今回の構成では、`fastapi-app` をアプリケーションホスト相当として扱い、ホスト内部で見える認証状態や token 状態を `audit_events` に残します。追加で `hids-hips` コンテナを置き、FastAPI ソースの改ざん検知と内部ヘルスチェックを行います。

## 現在の実装

- ログイン失敗回数を `users.failed_login_count` に保存する
- 一定回数を超えたユーザーを `users.locked_until` で一時ロックする
- refresh token を `refresh_tokens` に HMAC hash として保存する
- refresh token のローテーションと logout 時の失効を記録する
- 本人用 `/api/auth/audit-events` と管理者用 `/api/security/audit-events` で監査イベントを確認する
- `hids-hips` コンテナが `fastapi/app` を読み取り専用で監視する
- `hids-hips` コンテナが `fastapi-app:8000/api/health` を内部ネットワークから確認する
- 検知結果を `logs/hids/alerts.log` に JSON Lines で保存する
- 検知結果を `POST /api/internal/security-events` に送信し、`audit_events` にも保存する
- baseline を `logs/hids/baseline.json` に保存し、再起動後も継続して比較する

## HIDS/HIPS として見るシグナル

- アカウントロック
- refresh token の再利用拒否
- logout による refresh token 失効
- 権限付き監査 API へのアクセス
- FastAPI ソースファイルの作成・削除・変更
- 内部ヘルスチェック失敗

## HIDS と HIPS の分担

- `HIDS`
  - `hids-hips` コンテナによるファイル改ざん検知とヘルスチェック
  - `audit_events` による認証・監査イベントの可視化
- `HIPS`
  - FastAPI 側のアカウントロック
  - refresh token ローテーション済み token の再利用拒否
  - logout 済み refresh token の拒否

Docker 学習環境では、OS カーネルレベルのプロセス強制停止や syscall 制御までは再現していません。そのため HIPS は、アプリケーション内部での拒否制御として実装しています。

## 保存されるイベント

`audit_events.action` には次のような値を保存します。

- `hids_file_created`
- `hids_file_modified`
- `hids_file_deleted`
- `hids_health_degraded`
- `hids_health_unreachable`
- `sensor_heartbeat`

`details.component` は `hids-hips` になります。これにより、`/api/security/monitoring/summary` の `sensor_counts` で HIDS/HIPS 由来の検知を確認できます。

## 実運用での発展形

本番寄りにする場合は、次を追加します。

- ファイル改ざん検知
- プロセス監視
- OS 認証ログの収集
- コンテナ runtime 監視
- Wazuh, osquery, auditd などとの連携

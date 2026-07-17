# HIDS / HIPS / NIDS Addition Record

このドキュメントは、`CURRENT_ARCHITECTURE.md` を理想構成の基準として、現行 Docker 構成へ HIDS、HIPS、NIDS を追加した内容を記録するためのものです。

## 判断した方針

既存の通信本線は維持します。

```text
Client
  -> external-firewall
  -> nips
  -> waf
  -> reverse-proxy
  -> internal-firewall
  -> fastapi-app
  -> postgres
```

`NIDS` は本線上で通信を止める装置ではなく、横から観測する IDS として扱います。  
そのため、`nips` のような inline proxy にはせず、既存の境界ログを読み取る監視専用コンテナとして追加しました。

`HIDS / HIPS` は `fastapi-app` をアプリケーションホスト相当として扱います。  
HIDS はファイル改ざん検知とヘルスチェック、HIPS は FastAPI 側のアカウントロックや refresh token 再利用拒否など、アプリケーション内部の拒否制御として整理しました。

## 追加した Compose サービス

### `nids`

`nids` は network IDS 相当のログ監視センサーです。

- 通信本線には入らない
- `ports` を持たないため外部公開されない
- `edge_net`, `app_net`, `api_net` に接続する
- `api_net` 経由で `fastapi-app` の内部 ingest API に検知結果を送信する
- 次のログを読み取り専用で監視する
  - `logs/external-firewall`
  - `logs/waf`
  - `logs/nginx`
  - `logs/internal-firewall`
- 検知結果を `logs/nids/alerts.log` に JSON Lines で出力する
- 検知結果を `fastapi-app` の内部 API に送信し、PostgreSQL の `audit_events` にも保存する
- 読み取り位置を `logs/nids/state.json` に保存し、再起動後の重複読み取りを抑える

検知対象は次です。

- `403`, `405`, `429` などの遮断系 status
- SQL Injection 風の文字列
- XSS 風の文字列
- `.git`, `/etc/passwd`, `wp-admin`, `phpmyadmin` などの探索
- `sqlmap`, `nikto`, `nmap`, `masscan` などの scanner User-Agent

### `hids-hips`

`hids-hips` は host IDS / host IPS 相当の監視・保護センサーです。

- 通信本線には入らない
- `ports` を持たないため外部公開されない
- `api_net` にのみ接続する
- `fastapi/app` を読み取り専用で監視する
- `fastapi-app:8000/api/health` を内部ネットワークから確認する
- 検知結果を `logs/hids/alerts.log` に JSON Lines で出力する
- 検知結果を `fastapi-app` の内部 API に送信し、PostgreSQL の `audit_events` にも保存する
- ファイル baseline を `logs/hids/baseline.json` に保存し、再起動後も前回 baseline と比較する

HIDS として見るものは次です。

- FastAPI ソースファイルの作成
- FastAPI ソースファイルの削除
- FastAPI ソースファイルの変更
- 内部ヘルスチェック失敗

HIPS として見るものは次です。

- ログイン失敗回数によるアカウントロック
- refresh token ローテーション済み token の再利用拒否
- logout 済み refresh token の拒否
- 権限付き監査 API による監査イベント確認

## 追加・変更したファイル

### 実装

- `nids/Dockerfile`
  - NIDS センサー用 Python コンテナ
- `nids/monitor.py`
  - 境界ログを読み、疑わしい行を `logs/nids/alerts.log` に出力する
  - `NIDS_INGEST_URL` に検知イベントを送信する
  - `NIDS_STATE_PATH` にログ offset を保存する
- `hids/Dockerfile`
  - HIDS/HIPS センサー用 Python コンテナ
- `hids/monitor.py`
  - FastAPI ソースの SHA-256 baseline を作り、変更を検知する
  - 内部ヘルスチェックも実行する
  - `HIDS_INGEST_URL` に検知イベントを送信する
  - `HIDS_BASELINE_PATH` に baseline を保存する
- `docker-compose.yml`
  - `nids` サービスを追加
  - `hids-hips` サービスを追加
  - `SECURITY_SENSOR_TOKEN` による内部 API 認証を追加
- `fastapi/app/main.py`
  - `POST /api/internal/security-events` を追加
  - センサーイベントを `audit_events` に保存する
  - `/api/security/monitoring/summary` に `sensor_counts` を追加
- `logs/nids/.gitkeep`
  - NIDS アラート出力先を保持
- `logs/hids/.gitkeep`
  - HIDS/HIPS アラート出力先を保持

### ドキュメント

- `CURRENT_ARCHITECTURE.md`
  - `NIDS planned` を `NIDS log sensor` に変更
  - `HIDS / HIPS planned` を `HIDS / HIPS host sensor` に変更
  - 監視先を planned ではなく log / host monitor として整理
- `README.md`
  - NIDS / HIDS / HIPS を追加済みの監視レイヤーとして説明
  - `logs/nids/alerts.log` と `logs/hids/alerts.log` を確認ポイントに追加
- `nids/README.md`
  - 独立 NIDS コンテナの役割、監視ログ、配置を追記
- `hids/README.md`
  - 独立 HIDS/HIPS コンテナの役割、HIDS と HIPS の分担を追記
- `logs/README.md`
  - `logs/nids` と `logs/hids` を追加
- `security-monitoring-addition.md`
  - この追加作業の記録

## 現行構成との対応

`CURRENT_ARCHITECTURE.md` の理想構成に対して、今回の Docker 構成は次のように対応します。

| 理想構成の要素 | Docker 上の対応 | 状態 |
| --- | --- | --- |
| External Firewall | `external-firewall` | 実装済み |
| NIPS | `nips` | 実装済み |
| WAF | `waf` | 実装済み |
| Reverse Proxy | `reverse-proxy` | 実装済み |
| Internal Firewall | `internal-firewall` | 実装済み |
| Backend Application | `fastapi-app` | 実装済み |
| Database | `postgres` | 実装済み |
| NIDS | `nids` | 追加済み |
| HIDS / HIPS | `hids-hips` + FastAPI 側の拒否制御 | 追加済み |
| API Gateway | 未分離 | 今後の候補 |

## センサーイベントの保存先

本格実装版では、センサーの検知結果を二重に残します。

1. ローカルログ
   - `logs/nids/alerts.log`
   - `logs/hids/alerts.log`
2. PostgreSQL
   - `audit_events`
   - `action` は `nids_*`, `hids_*`, `sensor_heartbeat`
   - `details.component` に `nids` または `hids-hips` を保存

FastAPI には内部専用の ingest API を追加しています。

```text
POST /api/internal/security-events
Header: X-Sensor-Token: <SECURITY_SENSOR_TOKEN>
```

この API は `SECURITY_SENSOR_TOKEN` が一致した場合だけイベントを受け付けます。Docker Compose では学習用の固定値 `dev-security-sensor-token` を使っています。本番では secret manager や `.env` で差し替える前提です。

## 検証記録

実行済みの確認は次です。

```bash
python3 -m py_compile nids/monitor.py hids/monitor.py fastapi/app/main.py
docker compose config
```

結果:

- `nids/monitor.py`, `hids/monitor.py`, `fastapi/app/main.py` の Python 構文チェックは成功
- `docker compose config` は成功
- `docker-compose.yml` とネットワーク、volume、depends_on の構文は解決できている

追加サービスの build 確認も試しました。

```bash
docker compose build nids hids-hips
```

ただし、この環境では Docker API への接続権限がなく、次の理由で停止しました。

```text
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

そのため、現時点で確認済みなのは静的検証までです。Docker socket 権限がある環境では、次で実行確認します。

```bash
docker compose up -d --build nids hids-hips
docker compose ps nids hids-hips
tail -f logs/nids/alerts.log
tail -f logs/hids/alerts.log
```

## この追加の限界

今回の NIDS は実 NIC の traffic mirror や packet capture ではありません。  
Docker 学習環境として、境界ログを横から読むことで「通信本線を止めない IDS」の役割を再現しています。

今回の HIDS/HIPS は OS カーネルレベルの auditd、eBPF、process kill、syscall block までは行いません。  
学習環境として、FastAPI ホスト相当のファイル改ざん検知と、アプリケーション内部の拒否制御を HIDS/HIPS として整理しています。

本番寄りにする場合は、次のような構成に発展させます。

- NIDS: Suricata, Zeek, traffic mirror, packet capture
- HIDS: Wazuh, osquery, auditd, file integrity monitoring
- HIPS: runtime policy enforcement, eBPF, AppArmor / SELinux, EDR
- Logs: Loki / Grafana, ELK, SIEM 連携

# Security review

現在の実装をコード・Compose・プロキシ設定・DB初期化SQLと突き合わせた結果です。本番認証ではなく、1台のLinux VM上で境界分離を学習・検証する構成です。

## 対応済み

- 外部公開ポートを`443/tcp`だけに限定
- external-firewallからNIPSへPROXY protocolを渡し、WAFの送信元単位レート制限と監査用アドレスを実クライアント単位に統一
- FastAPIのForwardedヘッダー信頼先を`internal-firewall`の固定IPに限定
- WAFで許可ルート、メソッド、入力サイズ、代表的な探索・注入シグナルを制限
- refresh tokenをHttpOnly Cookieへ格納し、DBにはHMAC値だけを保存してローテーション
- パスワードをPBKDF2-HMAC-SHA-256、個人情報をAES-256-GCMで保存
- DBをホスト公開せず、アプリ接続を制限付き`app_user`に限定
- センサーAPIを共有秘密トークンで保護し、監査イベントへ保存

## 未実装・本番導入前に必要

- `.env.example`の値を使わない秘密情報管理、鍵・DB資格情報・センサートークンのローテーション
- 認証試行をユーザー単位だけでなく、送信元・アカウント横断でも制限する分散rate limiter
- refresh token再利用時のfamily全体失効、CSRFトークンまたは厳格なOrigin検証
- DBバックアップの暗号化、監査ログの改ざん耐性、保持期間・削除方針、監視通知
- NIDSの実パケット監視、HIDSのauditd/eBPF/AppArmor/SELinux等によるOSレベルの保護
- コンテナの非root実行、read-only filesystem、capability削減、イメージ固定・脆弱性スキャン
- `image_url`を将来サーバー側で取得する場合のSSRF対策。現状はURL文字列を保存・返却するだけ
- 本番TLS証明書、HSTS、外部DNS・WAF・ホストfirewall・バックアップを含む運用設計

## 検証範囲

```bash
python3 -m py_compile fastapi/app/main.py nids/monitor.py hids/monitor.py
docker compose --env-file .env config --quiet
PYTHONPATH=fastapi python3 -m unittest discover -s fastapi/tests
```

Docker daemonが利用できる環境では、さらに各コンテナの設定検証、起動後の外部経路テスト、DB保存値の確認を実施します。

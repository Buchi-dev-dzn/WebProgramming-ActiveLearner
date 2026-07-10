# Auth Crypto Test

このディレクトリには、認証・暗号化・出品者プロフィール基盤を検証するためのテストを置きます。

## 目的

- ユーザー登録ができるか
- 同じ email を重複登録できないか
- 誤 password を `401` で拒否するか
- 正しい password で login し、JWT を返すか
- Bearer token 付きで `/api/auth/me` が通るか
- token なしの `/api/auth/me` を `401` で拒否するか
- seller ユーザーが出品者プロフィールを作成・取得できるか
- 任意で DB 内に平文 email / password が保存されていないか確認する

## スクリプト

- [check_auth_crypto.py](/home/buchi/WebProgramming-ActiveLearner/other/test/auth-crypto/check_auth_crypto.py)

## 使い方

公開入口経由で API だけ確認する:

```bash
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1
```

JSON で出す:

```bash
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1 --json
```

DB 内の保存状態まで確認する:

```bash
python3 other/test/auth-crypto/check_auth_crypto.py 127.0.0.1 --check-db --compose-dir /home/buchi/WebProgramming-ActiveLearner
```

## 期待値

- `register_seller`
  - `201`
  - 登録した email と `role=seller` を返す
- `duplicate_email_rejected`
  - `409`
  - 大文字小文字を変えた同一 email も重複として扱う
- `bad_password_rejected`
  - `401`
- `login_issues_jwt`
  - `200`
  - `access_token` が JWT 形式で返る
- `auth_me_accepts_bearer_token`
  - `200`
  - `X-Request-Id` が本文に伝播する
- `auth_me_requires_token`
  - `401`
- `seller_profile_upsert`
  - `200`
  - 出品者プロフィールを作成または更新する
- `seller_profile_get`
  - `200`
  - business email を復号した値として返す
- `db_plaintext_inspection`
  - `--check-db` 指定時は DB 内の `email_ciphertext`, `email_lookup_hash`, `password_hash` に平文 email / password が含まれないことを見る

## 注意

- このテストは毎回ランダムな email を作成するため、DB にテストユーザーが残ります
- `--check-db` は `docker compose exec postgres` を使うため、Docker API にアクセスできる環境で実行してください
- HTTPS は自己署名証明書のため、スクリプト内で証明書検証を無効化しています
- DB 保存確認は最低限の平文混入チェックです。暗号強度の証明ではなく、保存方針の実装ミスを見つけるための検査です

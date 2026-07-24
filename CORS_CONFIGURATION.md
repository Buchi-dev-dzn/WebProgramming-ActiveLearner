# FastAPIのCORS設定

## 1. CORSとは

CORS（Cross-Origin Resource Sharing）は、ブラウザが異なるオリジンのWeb APIへアクセスしてよいかを、API側がレスポンスヘッダーで示す仕組みです。

オリジンは次の3要素の組み合わせです。

```text
scheme + host + port
```

したがって、次のURLはそれぞれ別オリジンです。

```text
https://localhost:5173
https://127.0.0.1:5173
https://localhost:3000
https://192.168.64.4
```

例えば、Viteの開発画面を`https://localhost:5173`で開き、APIを`https://192.168.64.4/api/products`で直接呼び出す場合はクロスオリジン通信になります。

CORSはブラウザが実施する制御です。`curl`やサーバー間通信を認証する仕組みではなく、認証・認可やWAFの代わりにはなりません。

## 2. 現在の設定

FastAPIは環境変数`CORS_ALLOWED_ORIGINS`をカンマ区切りで読み取ります。

`docker-compose.yml`の開発用設定:

```yaml
environment:
  CORS_ALLOWED_ORIGINS: "https://localhost:5173,https://127.0.0.1:5173"
```

これにより、次のVite開発サーバーを許可しています。

- `https://localhost:5173`
- `https://127.0.0.1:5173`

末尾のスラッシュは付けません。実際にブラウザのアドレスバーで使用するオリジンと完全に一致させる必要があります。

環境変数が空または未設定の場合、FastAPIにCORS middlewareは追加されません。この場合、同一オリジン通信とCORSの対象外であるサーバー間通信は引き続き利用できます。

## 3. FastAPI側の処理

`fastapi/app/main.py`では、FastAPI標準の`CORSMiddleware`を使用しています。

設定内容:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
    max_age=600,
)
```

各項目の意味:

| 設定 | 内容 |
| --- | --- |
| `allow_origins` | 通信を許可するフロントエンドのオリジン |
| `allow_credentials=True` | refresh tokenのHttpOnly Cookieを送受信できるようにする |
| `allow_methods` | CORSで許可する実APIのHTTPメソッド |
| `allow_headers` | ブラウザから送信してよいリクエストヘッダー |
| `expose_headers` | JavaScriptから読み取ってよいレスポンスヘッダー |
| `max_age=600` | プリフライト結果をブラウザが最大10分キャッシュできる |

現在の認証はaccess tokenを`Authorization: Bearer ...`で送り、refresh tokenをHttpOnly Cookieで管理します。そのため、`allow_credentials`は`True`です。許可オリジンにワイルドカードは使用できず、信頼するフロントエンドURLを明示します。

## 4. プリフライトリクエスト

ブラウザがJSONのPOSTや`Authorization`ヘッダー付きリクエストを送る前に、送信先へ`OPTIONS`リクエストを送ることがあります。これをプリフライトと呼びます。

例:

```http
OPTIONS /api/auth/login HTTP/1.1
Origin: http://localhost:5173
Access-Control-Request-Method: POST
Access-Control-Request-Headers: content-type
```

許可されたオリジンなら、FastAPIのCORS middlewareが次のようなヘッダーを返します。

```http
Access-Control-Allow-Origin: http://localhost:5173
Access-Control-Allow-Methods: GET, POST
Access-Control-Allow-Headers: ...
```

このリポジトリではFastAPIの前にWAFがあるため、WAFもプリフライトを通す必要があります。`waf/conf.d/default.conf`では、外部公開しているAPIルートに限って`OPTIONS`を許可しています。

内部センサー用の次のAPIは、ブラウザ向け許可ルートに含めていません。

```text
POST /api/internal/security-events
```

そのため、CORS設定を利用してブラウザからセンサーAPIへアクセスすることはできません。

## 5. フロントエンドからの利用例

許可済みの`https://localhost:5173`で動作するフロントエンドから、VMのAPIを直接呼び出す例:

```javascript
const response = await fetch("https://192.168.64.4/api/auth/login", {
  method: "POST",
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    email: "user@example.com",
    password: "example-password",
  }),
});

if (!response.ok) {
  throw new Error(`Request failed: ${response.status}`);
}

const session = await response.json();
```

ただし、`localhost`からVMのIPへ直接接続するとcross-siteになるため、開発用の`SameSite=Lax` Cookieを使ったrefresh認証には適しません。現在のReact開発構成ではVite proxyを使い、ブラウザからは`fetch("/api/...")`で接続してください。上の直接接続例はCORSヘッダーの確認用途です。

access tokenを送る例:

```javascript
const response = await fetch("https://192.168.64.4/api/auth/me", {
  credentials: "include",
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});
```

Vite proxyを使用して`fetch("/api/...")`とする場合、ブラウザからViteへの通信は同一オリジンです。ViteからAPIへの転送はサーバー間通信なので、通常はCORS制御の対象になりません。

## 6. 許可オリジンの変更

React開発サーバーを`https://localhost:3000`で起動する場合:

```yaml
CORS_ALLOWED_ORIGINS: "https://localhost:3000"
```

複数の開発URLを許可する場合:

```yaml
CORS_ALLOWED_ORIGINS: "https://localhost:3000,https://localhost:5173,https://127.0.0.1:5173"
```

本番フロントエンドが`https://shop.example.com`の場合:

```yaml
CORS_ALLOWED_ORIGINS: "https://shop.example.com"
```

設定変更後はFastAPIコンテナを再作成します。

```bash
docker compose up -d --build --force-recreate fastapi-app
```

WAF設定も変更した場合は、WAFも再作成します。

```bash
docker compose up -d --force-recreate waf
```

## 7. ワイルドカードを使わない理由

次のようにすべてのオリジンを許可する設定は使用していません。

```text
*
```

公開範囲が不必要に広くなり、意図しないWebサイトからAPIを呼び出せるためです。開発環境でも、実際に使用するフロントエンドのオリジンだけを列挙します。

CORSで許可したサイトからであっても、権限が必要なAPIではaccess tokenの検証が引き続き行われます。credential付きCORSではワイルドカードオリジンを使用できません。

## 8. 動作確認

### 許可されたオリジン

```bash
curl -k -i \
  -X OPTIONS \
  -H "Origin: https://localhost:5173" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  https://127.0.0.1/api/auth/login
```

レスポンスに次のヘッダーが含まれていることを確認します。

```text
Access-Control-Allow-Origin: https://localhost:5173
```

### 許可されていないオリジン

```bash
curl -k -i \
  -X OPTIONS \
  -H "Origin: https://untrusted.example" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  https://127.0.0.1/api/auth/login
```

許可されていないプリフライトはFastAPIによって拒否され、ブラウザから実APIリクエストは送信されません。

### GETレスポンス

```bash
curl -k -i \
  -H "Origin: https://localhost:5173" \
  https://127.0.0.1/api/health
```

この場合も`Access-Control-Allow-Origin`が返ることを確認します。

## 9. よくある問題

### CORSエラーに見えるがAPIが停止している

ブラウザはレスポンスをJavaScriptへ渡せない場合、詳細をCORSエラーとして表示することがあります。次の順で確認します。

1. `curl -k https://127.0.0.1/api/health`が成功するか
2. `docker compose ps`で各サービスが起動しているか
3. ブラウザのNetworkタブで`OPTIONS`のステータスを確認する
4. リクエストの`Origin`が設定値と完全一致しているか確認する

### `localhost`と`127.0.0.1`

ブラウザ上では別オリジンです。両方を使用する場合は両方を許可します。

### HTTPとHTTPS

`http://localhost:5173`と`https://localhost:5173`は別オリジンです。現在はHTTPSオリジンだけを許可し、外部ポート`80`も公開していません。HTTPSページからHTTP APIを呼ぶ通信はMixed ContentとしてCORS処理より前に遮断されます。

### OPTIONSが404または405になる

FastAPIより前のWAFで対象ルートが許可されているか確認します。新しい公開APIを追加した場合は、通常のGET/POSTルートだけでなく、`OPTIONS`の許可対象も更新する必要があります。

## 10. 運用方針

- 開発時は利用する開発サーバーのオリジンだけを許可する
- 本番では本番フロントエンドのHTTPSオリジンだけを許可する
- 内部APIをCORSの公開対象にしない
- 新しいAPIメソッドやヘッダーを追加した際はFastAPIとWAFの両方を確認する
- Cookie認証へ変更する際はCORSだけでなくCSRF対策も設計する
- 可能であれば本番環境は同一オリジンで構成し、CORS依存を減らす

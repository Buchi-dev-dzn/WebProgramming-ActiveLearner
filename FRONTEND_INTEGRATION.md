# フロントエンドから FastAPI を利用する方法

> 状態: 連携仕様書です。React/Vite の実装自体はこのリポジトリに含まれていません。2026-07-24 時点の FastAPI・WAF・CORS 設定に合わせたクライアント実装の指針を示します。

## 1. 最初に理解しておくこと

この構成では、フロントエンドから `fastapi-app:8000` に直接アクセスしません。

外部からのリクエストは、必ず次のセキュリティ層を通します。

```text
Frontend / Browser
  -> external-firewall :443
  -> nips
  -> waf
  -> reverse-proxy
  -> internal-firewall
  -> fastapi-app :8000
  -> postgres
```

したがって、ブラウザから使用するAPIのベースURLは次のようになります。

```text
https://192.168.64.4/api

同じマシンから確認する場合:
https://127.0.0.1/api
```

`172.16.31.182` は現在のドキュメントで想定しているVMのIPです。環境が異なる場合は、実際のホスト名またはIPに置き換えてください。

Docker内部の次のアドレスは、ブラウザから使用するURLではありません。

```text
http://fastapi-app:8000
http://172.31.0.20:8000
```

これらはDocker内部ネットワーク専用です。

## 2. 推奨するフロントエンド構成

### 同一オリジン構成

FastAPIには、`CORS_ALLOWED_ORIGINS`で明示的に許可した開発用オリジンからアクセスできるCORS設定があります。設定の仕組みと変更方法は、[CORS_CONFIGURATION.md](./CORS_CONFIGURATION.md)を参照してください。

異なるオリジンからのアクセスも可能ですが、本番環境ではフロントエンドとAPIを同じオリジンで公開する構成が最も扱いやすく、許可オリジンの管理も減らせます。

例:

```text
Frontend: https://example.local/
API:      https://example.local/api/health
```

フロントエンドでは絶対URLではなく、次のような相対URLを使用します。

```javascript
const response = await fetch("/api/health");
```

この方式なら、開発環境と本番環境でホスト名が変わっても、フロントエンドのAPI URLを変更せずに済みます。

ただし、現在のWAFとreverse proxyは `/` でフロントエンドの静的ファイルを配信していません。実際に同一オリジン化する際は、次のいずれかの追加実装が必要です。

- reverse proxyまたは専用フロントエンドコンテナで静的ファイルを配信する
- `/api/` は現在のFastAPIへ、それ以外はフロントエンドへ振り分ける
- React/Viteなどの開発サーバ側で `/api` をVMへproxyする

### Vite開発サーバを使用する場合

ViteからVM上のAPIへproxyすると、ブラウザ上は同一オリジンとして扱えます。

`vite.config.js` の例:

```javascript
import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: "https://192.168.64.4",
        changeOrigin: true,
        secure: false, // 開発用自己署名証明書を使う場合のみ
      },
    },
  },
});
```

Reactなどのコードからは、proxy先を意識せず相対URLで呼び出します。

```javascript
const response = await fetch("/api/products");
```

## 3. 基本的なリクエスト形式

JSONを送るAPIでは、次のヘッダーを指定します。

```http
Content-Type: application/json
```

ログイン後のAPIでは、access tokenを次の形式で送ります。

```http
Authorization: Bearer <access_token>
```

共通のJavaScript関数を用意すると呼び出しやすくなります。

```javascript
const API_BASE_URL = "/api";

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const error = new Error(`API request failed: ${response.status}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }

  return body;
}
```

GETリクエストの例:

```javascript
const health = await apiRequest("/health");
const products = await apiRequest("/products?limit=20&offset=0");
```

POSTリクエストの例:

```javascript
const product = await apiRequest("/products", {
  method: "POST",
  body: JSON.stringify({
    sku: "ITEM-001",
    name: "Sample Item",
    price_cents: 1500,
    stock: 10,
  }),
});
```

## 4. 認証の流れ

基本的な認証フローは次のとおりです。

```text
ユーザー登録
  -> ログイン
  -> access tokenをレスポンスで受け取り、refresh tokenをCookieで受け取る
  -> access tokenをAuthorizationヘッダーに付ける
  -> access token期限切れ時にHttpOnly Cookieを使ってrefreshする
  -> ログアウト時にサーバー上のrefresh tokenとCookieを失効させる
```

access tokenの有効期間は15分、refresh tokenの有効期間は14日です。

### 4.1 ユーザー登録

```javascript
const result = await apiRequest("/auth/register", {
  method: "POST",
  body: JSON.stringify({
    email: "user@example.com",
    password: "example-password",
    role: "customer",
  }),
});
```

出品者として登録する場合は、`role` を `seller` にします。

```json
{
  "email": "seller@example.com",
  "password": "example-password",
  "role": "seller"
}
```

登録処理だけではtokenは発行されないため、登録後にログインが必要です。

### 4.2 ログイン

```javascript
const session = await apiRequest("/auth/login", {
  method: "POST",
  body: JSON.stringify({
    email: "user@example.com",
    password: "example-password",
  }),
});

const accessToken = session.access_token;
```

主なレスポンス:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_expires_in": 1209600,
  "user": {
    "id": 1,
    "email": "user@example.com",
    "role": "customer"
  }
}
```

### 4.3 ログイン中のユーザーを取得

```javascript
const me = await apiRequest("/auth/me", {
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});
```

### 4.4 access tokenを更新

```javascript
const refreshed = await apiRequest("/auth/refresh", {
  method: "POST",
});

const newAccessToken = refreshed.access_token;
```

refresh tokenはHttpOnly Cookieから自動送信され、新しいCookieへローテーションされます。JavaScriptからrefresh tokenを読み書きする必要はありません。

### 4.5 ログアウト

```javascript
await apiRequest("/auth/logout", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});
```

ログアウト後は、フロントエンドがメモリに保持しているaccess tokenも削除します。refresh token CookieはAPIが削除します。

## 5. tokenの保存について

現在のAPIは、access tokenをJSONレスポンスで返し、refresh tokenをHttpOnly Cookieとして設定します。

access tokenはJavaScriptのメモリ上に保持します。

```javascript
let session = {
  accessToken: null,
};
```

ページを再読み込みしてaccess tokenが失われた場合は、`POST /api/auth/refresh`を呼び出して新しいaccess tokenを取得します。refresh tokenはHttpOnlyなので、JavaScriptや`localStorage`から読み取れません。

開発環境もHTTPSのみを使用し、Cookieの`Secure`属性を有効にしています。Composeでは`REFRESH_COOKIE_SECURE=true`です。

## 6. 商品API

### 商品一覧

```javascript
const result = await apiRequest("/products?limit=20&offset=0");
console.log(result.items);
```

### SKUで商品を取得

```javascript
const sku = encodeURIComponent("ITEM-001");
const result = await apiRequest(`/product?sku=${sku}`);
```

### 商品を登録

```javascript
const result = await apiRequest("/products", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
  body: JSON.stringify({
    sku: "ITEM-001",
    name: "Sample Item",
    price_cents: 1500,
    stock: 10,
  }),
});
```

### 在庫を更新

```javascript
const result = await apiRequest("/product/stock", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
  body: JSON.stringify({
    sku: "ITEM-001",
    stock: 8,
  }),
});
```

商品登録と在庫更新には、`seller`または`admin`のaccess tokenが必要です。フロントエンドの表示制御に加えて、FastAPI側でも権限を検証します。

## 7. 出品者プロフィールAPI

プロフィール登録・更新には、`seller`または`admin`のaccess tokenが必要です。

```javascript
const profile = await apiRequest("/seller/profile", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
  body: JSON.stringify({
    store_name: "Example Store",
    store_description: "サンプル店舗です",
    business_email: "shop@example.com",
    phone: "090-1234-5678",
    business_address: "東京都...",
    payout_account_token: "payment-provider-token",
  }),
});
```

`payout_account_token` は送信時だけ使用し、サーバーでは AES-256-GCM で暗号化されます。
取得レスポンスに token 自体は含まれず、登録状態を示す `has_payout_account_token` だけが返ります。

自分のプロフィールを取得:

```javascript
const profile = await apiRequest("/seller/profile", {
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});
```

## 8. 監査・監視API

自分自身の監査イベントは、ログインユーザーなら取得できます。

```javascript
const events = await apiRequest("/auth/audit-events?limit=25", {
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});
```

システム全体の監査イベントと監視サマリーには、`admin`または`support`権限が必要です。

```javascript
const summary = await apiRequest("/security/monitoring/summary", {
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});
```

`POST /api/internal/security-events` はNIDS/HIDSコンテナ専用です。センサートークンをブラウザへ渡すことになるため、フロントエンドから呼び出してはいけません。

## 9. HTTPステータスとエラー処理

フロントエンドでは、少なくとも次のステータスを処理してください。

| ステータス | 主な意味 | フロントエンドでの対応例 |
| --- | --- | --- |
| `200` | 成功 | 画面へ結果を反映 |
| `201` | 登録成功 | 完了表示や一覧へ移動 |
| `401` | token不正・期限切れ | refreshを試し、失敗したらログイン画面へ |
| `403` | 権限不足またはWAF遮断 | 権限エラーを表示 |
| `404` | 対象またはルートが存在しない | Not Found表示 |
| `409` | emailやSKUの重複 | 入力項目に重複エラーを表示 |
| `422` | 入力値が不正 | 入力項目ごとにエラーを表示 |
| `423` | アカウントロック中 | 時間を置いて再試行するよう表示 |
| `429` | リクエスト過多 | 一定時間待って再試行 |
| `503` | DBまたは後段サービス停止 | 一時的な障害として表示 |

FastAPIの入力エラーは、一般的に次のような形式です。

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "password"],
      "msg": "String should have at least 8 characters"
    }
  ]
}
```

アプリケーション独自エラーでは、次のように`detail.error`へ識別子が入ります。

```json
{
  "detail": {
    "error": "invalid_credentials"
  }
}
```

## 10. HTTPS開発時の注意

現在のHTTPS証明書は開発用の自己署名証明書です。SANには`localhost`、`127.0.0.1`、`192.168.64.4`が含まれます。公開CAからは信頼されないため、ブラウザでは証明書警告が表示されます。

ブラウザで警告なしに検証する場合は、開発端末の信頼ストアへ`waf/certs/dev.crt`だけを登録します。`waf/certs/dev.key`は秘密鍵なので、登録・配布しません。Vite開発サーバー自体もHTTPSで起動してください。

HTTPSで表示したフロントエンドからHTTP APIを呼ぶ通信はMixed Contentとして遮断されます。また、この構成では外部ポート`80`を公開していないため、API URLには必ず`https://`を使用します。

## 11. curlによる疎通確認

フロントエンドを実装する前に、API単体で疎通確認できます。

```bash
curl -k -i https://127.0.0.1/api/health
curl -k -i "https://127.0.0.1/api/products?limit=20&offset=0"
```

ユーザー登録:

```bash
curl -k -i \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"example-password","role":"customer"}' \
  https://127.0.0.1/api/auth/register
```

ログイン:

```bash
curl -k -i \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"example-password"}' \
  https://127.0.0.1/api/auth/login
```

認証が必要なAPI:

```bash
curl -k -i \
  -H "Authorization: Bearer ACCESS_TOKEN_HERE" \
  https://127.0.0.1/api/auth/me
```

開発用自己署名証明書をOSの信頼ストアへ登録していない場合、curlでの検証には`-k`が必要です。`-k`は証明書検証を無効化するため、開発時の疎通確認以外では使用しません。

```bash
curl -k -i https://127.0.0.1/api/health
```

## 12. 実装前の確認事項

フロントエンド実装を進める前に、次を決める必要があります。

1. React、Vue、Next.jsなど、どのフレームワークを使用するか
2. フロントエンドをどのコンテナまたはホストで配信するか
3. 開発中はVite等のproxyを使うか
4. 本番相当では同一オリジンにするか、CORSの許可オリジンを本番URLに限定するか
5. 本番環境でCookieの`Secure`属性を有効にしたか
6. 本番環境のCORS許可オリジンが本番URLだけに限定されているか

現在の確定方針は[REACT_FRONTEND_ARCHITECTURE.md](./REACT_FRONTEND_ARCHITECTURE.md)を参照してください。

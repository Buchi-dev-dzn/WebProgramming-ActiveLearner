# フロントエンドから FastAPI を利用する方法

## 1. 最初に理解しておくこと

この構成では、フロントエンドから `fastapi-app:8000` に直接アクセスしません。

外部からのリクエストは、必ず次のセキュリティ層を通します。

```text
Frontend / Browser
  -> external-firewall :80 / :443
  -> nips
  -> waf
  -> reverse-proxy
  -> internal-firewall
  -> fastapi-app :8000
  -> postgres
```

したがって、ブラウザから使用するAPIのベースURLは次のようになります。

```text
開発VMへHTTP接続する場合:
http://192.168.64.4/api

開発VMへHTTPS接続する場合:
https://192.168.64.4/api

同じマシンから確認する場合:
http://127.0.0.1/api
```

`192.168.64.4` は現在のドキュメントで想定しているVMのIPです。環境が異なる場合は、実際のホスト名またはIPに置き換えてください。

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
        target: "http://192.168.64.4",
        changeOrigin: true,
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
  -> access tokenとrefresh tokenを受け取る
  -> access tokenをAuthorizationヘッダーに付ける
  -> access token期限切れ時にrefreshする
  -> ログアウト時にrefresh tokenを失効させる
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
const refreshToken = session.refresh_token;
```

主なレスポンス:

```json
{
  "access_token": "...",
  "refresh_token": "...",
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
  body: JSON.stringify({
    refresh_token: refreshToken,
  }),
});

const newAccessToken = refreshed.access_token;
const newRefreshToken = refreshed.refresh_token;
```

refreshを実行するとrefresh tokenも新しくなります。古いrefresh tokenは再利用できないため、フロントエンド側で必ず新しい値に置き換えてください。

### 4.5 ログアウト

```javascript
await apiRequest("/auth/logout", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
  body: JSON.stringify({
    refresh_token: refreshToken,
  }),
});
```

ログアウト後は、フロントエンドが保持しているaccess tokenとrefresh tokenも削除します。

## 5. tokenの保存について

現在のAPIはtokenをJSONレスポンスで返し、Cookieは設定しません。そのため、フロントエンド側で保存方法を決める必要があります。

簡単な学習用実装では、JavaScriptのメモリ上に保持できます。

```javascript
let session = {
  accessToken: null,
  refreshToken: null,
};
```

ページ再読み込み後もログイン状態を維持するために`localStorage`へrefresh tokenを保存する方法もありますが、XSS発生時に読み取られる危険があります。

本番向けには、次の方式への変更を推奨します。

- refresh tokenを`HttpOnly`、`Secure`、`SameSite`付きCookieで管理する
- access tokenは短時間だけメモリに保持する
- Content Security Policyをフロントエンドに合わせて設計する

現状のAPIはCookie認証を実装していないため、この方式にする場合はFastAPI側の変更が必要です。

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
  body: JSON.stringify({
    sku: "ITEM-001",
    stock: 8,
  }),
});
```

現状では商品登録と在庫更新に認証・権限制御がありません。フロントエンドでボタンを非表示にするだけではセキュリティ対策にならないため、本格的に使用する前にFastAPI側でseller/admin権限を要求する必要があります。

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

現在のHTTPS証明書は開発用の自己署名証明書です。ブラウザから初回アクセスした際に、証明書警告が表示される可能性があります。

また、HTTPSで表示したフロントエンドからHTTPのAPIを呼び出すと、Mixed Contentとしてブラウザに遮断されます。フロントエンドとAPIは両方ともHTTP、または両方ともHTTPSに揃えてください。

## 11. curlによる疎通確認

フロントエンドを実装する前に、API単体で疎通確認できます。

```bash
curl -i http://127.0.0.1/api/health
curl -i "http://127.0.0.1/api/products?limit=20&offset=0"
```

ユーザー登録:

```bash
curl -i \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"example-password","role":"customer"}' \
  http://127.0.0.1/api/auth/register
```

ログイン:

```bash
curl -i \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"example-password"}' \
  http://127.0.0.1/api/auth/login
```

認証が必要なAPI:

```bash
curl -i \
  -H "Authorization: Bearer ACCESS_TOKEN_HERE" \
  http://127.0.0.1/api/auth/me
```

HTTPSの場合は、開発用自己署名証明書を使用しているため、検証時に`-k`が必要です。

```bash
curl -k -i https://127.0.0.1/api/health
```

## 12. 実装前の確認事項

フロントエンド実装を進める前に、次を決める必要があります。

1. React、Vue、Next.jsなど、どのフレームワークを使用するか
2. フロントエンドをどのコンテナまたはホストで配信するか
3. 開発中はVite等のproxyを使うか
4. 本番相当では同一オリジンにするか、CORSの許可オリジンを本番URLに限定するか
5. tokenを一時的にメモリへ置くか、Cookie認証へ変更するか
6. 商品登録・在庫更新をseller/adminだけに制限するか

現在の構成を維持するなら、まずは「Vite開発サーバの`/api` proxy + フロントエンドでは相対URL」という構成が最も簡単です。

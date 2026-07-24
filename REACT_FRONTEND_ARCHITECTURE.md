# Reactフロントエンドの推奨構成

> 状態: 将来実装向けの設計案です。2026-07-24 時点では React/Vite のソース、`package.json`、Vite 設定はリポジトリに存在しません。「確定済みの実装」ではありません。

## 推奨方針

このプロジェクトのフロントエンドは、次の構成を前提にします。

| 項目 | 採用内容 |
| --- | --- |
| UI | React |
| ビルド・開発サーバー | Vite |
| 開発時の配置 | Macの`https://localhost:5173` |
| 開発時のAPI接続 | Viteの`/api` proxy |
| 本番CORS | 本番フロントエンドURLだけを許可 |
| access token | Reactアプリのメモリに保持 |
| refresh token | HttpOnly Cookie |
| 商品登録・在庫更新 | `seller`または`admin`だけ許可 |

## 開発時の通信

```text
Browser
  -> https://localhost:5173
  -> Vite proxy /api
  -> https://192.168.64.4
  -> external-firewall
  -> NIPS
  -> WAF
  -> reverse-proxy
  -> internal-firewall
  -> FastAPI
```

ブラウザ側では相対URLを使用します。

```javascript
fetch("/api/products");
```

Viteの設定例:

```javascript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "https://192.168.64.4",
        changeOrigin: true,
        secure: false, // 開発用の自己署名証明書を使用する場合のみ
      },
    },
  },
});
```

Vite proxyを使うと、ブラウザから見た接続先は`localhost:5173`のままです。Cookieも`localhost`に対して保存され、ViteがAPIへのリクエストを中継します。

API側の自己署名証明書は`localhost`、`127.0.0.1`、`192.168.64.4`をSANに含みます。上の`secure: false`は、Vite proxyから開発用API証明書を検証せずに接続するための開発専用設定です。本番では使用しません。

ブラウザから接続するVite開発サーバー自体もHTTPS設定が必要です。API側の秘密鍵を別端末へコピーして流用せず、Mac側で`mkcert`などを使ってVite用のローカル証明書を用意してください。

## 認証状態

```text
login
  -> JSONでaccess tokenを受け取る
  -> HttpOnly Cookieでrefresh tokenを受け取る

通常API
  -> Authorization: Bearer <access token>

ページ再読み込み・access token期限切れ
  -> POST /api/auth/refresh
  -> Cookieが自動送信される
  -> 新しいaccess tokenをメモリへ保存
  -> refresh token Cookieもローテーション

logout
  -> POST /api/auth/logout
  -> DB上のrefresh tokenを失効
  -> Cookieを削除
  -> Reactのaccess tokenも削除
```

共通リクエスト関数では`credentials: "include"`を指定します。

```javascript
export async function apiRequest(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  const body = await response.json();
  if (!response.ok) {
    throw Object.assign(new Error("API request failed"), {
      status: response.status,
      body,
    });
  }
  return body;
}
```

## React起動時のセッション復元

access tokenはメモリだけに保持するため、ページ再読み込み時には消えます。アプリ起動時にrefreshを一度試します。

```javascript
async function restoreSession() {
  try {
    const session = await apiRequest("/auth/refresh", {
      method: "POST",
    });
    return session.access_token;
  } catch (error) {
    if (error.status === 401) {
      return null;
    }
    throw error;
  }
}
```

refreshが401なら未ログインとして扱います。それ以外のネットワーク障害や503を未ログインと同一扱いにすると、障害時にログイン画面へ飛ばされるため区別します。

## 商品操作の権限

閲覧APIは未ログインでも利用できます。

```text
GET /api/products
GET /api/product?sku=...
```

更新APIはFastAPI側で`seller`または`admin`を要求します。

```text
POST /api/products
POST /api/product/stock
```

React側でもユーザーの`role`を見て操作ボタンを制御しますが、最終的な権限判定は必ずFastAPIが行います。

## 開発用Cookie設定

Composeでは次の値を使用します。

```yaml
REFRESH_COOKIE_SECURE: "true"
REFRESH_COOKIE_SAMESITE: "lax"
```

Mac上のVite開発サーバーもHTTPSで起動し、Cookieの`Secure`属性を常に有効にします。

Cookieの設定内容:

- 名前: `refresh_token`
- `HttpOnly`: 有効
- `Path`: `/api/auth`
- `SameSite`: `Lax`
- 有効期間: 14日
- JavaScriptからの読み取り: 不可

## 本番移行

本番環境ではHTTPSを前提とし、少なくとも次を変更します。

```yaml
CORS_ALLOWED_ORIGINS: "https://frontend.example.com"
REFRESH_COOKIE_SECURE: "true"
REFRESH_COOKIE_SAMESITE: "lax"
```

フロントエンドとAPIが異なるサイトになる配置では、`SameSite=None`と`Secure=true`が必要になる場合があります。その場合は、CSRFトークンまたはOrigin検証も追加します。

可能なら本番では、次のように同一サイトまたは同一オリジンへまとめます。

```text
https://shop.example.com/
https://shop.example.com/api/
```

同一オリジンならCORSやcross-site Cookieに依存する範囲を減らせます。

## セキュリティ上の注意

- refresh tokenを`localStorage`やReact stateへ保存しない
- access tokenをURLやログへ出力しない
- 本番で`REFRESH_COOKIE_SECURE=false`を使用しない
- CORSに`*`を指定しない
- 401時のrefresh再試行は無限ループさせない
- refreshを複数同時実行しない
- 商品操作はUI表示だけでなくAPI側でも権限を検証する
- cross-site Cookie構成に変更する場合はCSRF対策を追加する

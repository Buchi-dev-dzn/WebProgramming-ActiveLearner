# External Firewall

このディレクトリには、Linux VM 上で `External Firewall` を擬似再現するための設定と補助スクリプトを置いています。

## この層の目的

- 外部から見える入口を 1 か所に固定する
- HTTPS用`443`のTCP接続だけを後段に渡す
- `reverse-proxy` を host に直接公開せず、必ず `external-firewall` を通す

## 現在のHTTPS限定方針

現在の`docker-compose.yml`はホスト側へ`443`だけを公開し、`external-firewall/nginx.conf`も`443`だけをlistenします。HTTP用`80`は接続不能とし、HTTPSへのリダイレクトも提供しません。

この文書の後半に残るHTTPコマンドは、HTTP/HTTPS併用時の過去の検証記録です。現在構成の疎通確認には`curl -k https://<VM_IP>/...`を使用してください。TLSはWAFで終端し、開発用自己署名証明書の詳細は[`waf/README.md`](../waf/README.md)を参照してください。

本来の構成では、External Firewall はクラウド firewall、NW 機器、security group、host firewall などで実現するのが自然です。  
今回は複数サーバーを用意できないため、Docker コンテナを使って「外周の入口サーバー」を擬似的に分けています。

## Firewall とは何か

今回の前提では、Firewall の目的は「許可しない通信を通さないこと」です。  
その代表的な実装が、L3/L4 のパケットフィルタリングです。

典型的には次の情報を見て `accept` / `drop` を決めます。

- 送信元 IP
- 宛先 IP
- プロトコル
- 宛先ポート
- 接続状態

この意味での Firewall は、`nftables` や `iptables` のように、カーネル層でパケットを判定して落とす実装を指すことが多いです。

## 今回の External Firewall はどういう実装か

今回の実装は、純粋なカーネルベース Firewall 1 つで完結しているわけではありません。  
次の 2 要素を組み合わせています。

### 1. 主実装: Docker 上の L4 gateway

`external-firewall`コンテナは`nginx stream`を使い、HTTPS用`443`だけをlistenして`nips`へTCP転送します。

これは次の意味を持ちます。

- 外部から入る TCP の入口を 1 か所に固定する
- `reverse-proxy` を host に直接公開させない
- `443`に来たTLS通信だけを後段へ渡す

ここで重要なのは、これは「パケットを自由にdropする汎用firewall」ではなく、`443`だけを受ける専用のL4 gatewayだということです。

### 2. 補助実装: host 側の packet filtering

`apply-nft.sh` は `nftables` を使って host の `input` chain にルールを入れます。

こちらは典型的な packet filtering に近い実装です。

- 許可ポート以外を `drop` する
- host 自体への到達性を絞る
- カーネル層でルールを適用する

ただし、このスクリプトは Docker 公開コンテナ向け通信すべてを完全代表するわけではありません。  
Docker の publish port は forwarding や NAT を伴うため、`input` chain だけで Docker 全体の公開経路を完全に支配できるとは限りません。

## `nginx stream` と `nftables` の違い

- `nginx stream`
  - ユーザ空間で動く TCP gateway
  - 受けた接続を後段に流す
  - 今回はHTTPS用`443`の入口分離を担当する
- `nftables`
  - カーネル層の packet filtering
  - 条件に合わない通信を `drop` できる
  - 今回は host 側補助ポリシーとして使う

つまり、今回の `External Firewall` は次のように整理できます。

- Docker 上の主役
  - `external-firewall` コンテナによる入口分離
- host 側の補助
  - `nftables` による packet filtering

## 報告書向けの言い方

今回の External Firewall は、純粋なカーネルベース packet filtering のみで構成したものではなく、Docker 上の L4 gateway と host 側 packet filtering を組み合わせて、外周入口を擬似再現した実装である。

この実装により、次の点を確認できる。

- 外部から見える入口を 1 か所に集約できること
- DMZ の reverse proxy を host 直公開から外せること
- host 側では必要に応じて packet filtering を補助的に適用できること

## 現在の実装

### 1. Docker 上の入口分離

現行のComposeでは、hostに公開するHTTPS用`443`は`external-firewall`だけです。

- `external-firewall`
  - hostで`443`を受ける
  - `nginx stream` で `reverse-proxy` に TCP 転送する
- `reverse-proxy`
  - host に publish しない
  - `edge_net` で `external-firewall` からだけ受ける

これにより、Docker 上では次のことが成り立ちます。

- 外部クライアントは `reverse-proxy` に直接到達できない
- 公開入口は `external-firewall` に集約される
- 外周サーバーと DMZ サーバーを別コンテナとして分離できる

### 2. optional な host firewall 補助

`apply-nft.sh` は host の `input` chain に許可ポートを入れる補助スクリプトです。

これは次の目的で使います。

- host 自体への到達性を `80/443` など必要最小限に絞る
- カーネル層の基本ポリシーを検証する

ただし、これは Docker 公開ポート制御の完全な代替ではありません。  
Docker の publish port は forwarding や NAT を伴うため、`input` chain だけで Docker 全体の公開経路を完全統制できるとは限りません。

そのため、このリポジトリでは次のように整理します。

- `external-firewall` コンテナ
  - Docker 上で外周入口を擬似再現する主役
- `apply-nft.sh`
  - host 側補助ポリシー
  - あくまで companion であり、主実装ではない

## 通信の流れ

現在の段階では次の流れです。

```text
Client
  -> host published port 443
  -> external-firewall (nginx stream)
  -> reverse-proxy
  -> internal-firewall
  -> fastapi-app
  -> postgres
```

将来的にはこの `external-firewall` と `reverse-proxy` の間、または前後に `NIPS` や `WAF` を追加していく想定です。

## 何が完成していて、何が未完成か

### 完成していること

- 外部公開の入口を `external-firewall` に集約した
- `443`だけをlistenするL4 gatewayとして分離した
- `reverse-proxy` を host 直公開から外した
- 外周層と DMZ 層を別コンテナで表現した

### まだ将来拡張が必要なこと

- `FW1` を host / cloud 側 firewall と完全に一致させること
- `NIPS` の導入
- `WAF` の再導入
- Docker の外にある本来のクラウド外周をどう表現するか

## ファイル

- `nginx.conf`
  - `443`を`nips`へTCP転送するL4 gateway設定
- `apply-nft.sh`
  - host `input` chain に許可ポートを入れる補助スクリプト
- `show-nft.sh`
  - 現在の table を表示する
- `remove-nft.sh`
  - 作成した table を削除する

## `apply-nft.sh` の使い方

デフォルトの許可ポート:

- `80`
- `443`

SSH を維持したい場合は `22` を明示的に追加します。

```bash
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
```

現在の状態確認:

```bash
./external-firewall/show-nft.sh
```

削除:

```bash
./external-firewall/remove-nft.sh
```

## `external-firewall` コンテナの操作

起動:

```bash
docker compose up -d external-firewall
```

停止:

```bash
docker compose stop external-firewall
```

再開:

```bash
docker compose start external-firewall
```

状態確認:

```bash
docker compose ps external-firewall reverse-proxy
```

ログ確認:

```bash
docker compose logs --tail=50 external-firewall reverse-proxy
```

## 比較しながら検証する考え方

この層は「有効時に通るもの」と「無効時にどう変わるか」を比較して確認すると理解しやすいです。  
特に比較したいのは次の 2 つです。

- Docker 上の入口分離
  - `external-firewall` コンテナを通さないと外から入れないか
- host 側 packet filtering 補助
  - `nftables` を入れたときに host 自体への到達性がどう変わるか

## 典型的な比較パターン

### 1. baseline

まず `external-firewall` は動かし、`nftables` は入れない状態を作ります。

```bash
docker compose up -d --build
./external-firewall/remove-nft.sh || true
```

この状態の意味:

- Docker 上の外周入口分離は有効
- host 側 packet filtering 補助は無効

期待値:

- `80/443` は `external-firewall` 経由で到達できる
- `reverse-proxy` は host 直公開していない
- `nftables` による host 側 drop はまだ入っていない

### 2. `nftables` 補助を有効化

```bash
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
```

この状態の意味:

- Docker 上の入口分離は有効
- host 側 packet filtering 補助も有効

期待値:

- `80/443` は引き続き到達できる
- 許可していない host 向けポートは `drop` されやすくなる
- ただし Docker 公開ポート全体の完全制御を保証するものではない

### 3. `external-firewall` コンテナだけ停止

```bash
docker compose stop external-firewall
```

この状態の意味:

- Docker 上の主実装を止めた
- host 側 `nftables` 補助だけ残っている可能性がある

期待値:

- `80/443` の公開入口が消える
- `reverse-proxy` は host 直公開していないため、外部から直接は到達できない
- つまり「外周サーバーが落ちると入口そのものが消える」ことを確認できる

### 4. 両方無効化

```bash
./external-firewall/remove-nft.sh || true
docker compose stop external-firewall
```

この状態の意味:

- host 側 packet filtering 補助も無効
- Docker 上の外周入口も停止

期待値:

- このリポジトリの現構成では、外部から `80/443` に入る入口は存在しない

## 比較時に見るコマンド

### L4 到達性

```bash
nc -vz <VM_IP> 80
nc -vz <VM_IP> 443
nc -vz -w 3 <VM_IP> 3001
```

### HTTP / HTTPS

```bash
curl -i http://<VM_IP>/health
curl -i http://<VM_IP>/api/health
curl -i http://<VM_IP>/api/info
curl -k -i https://<VM_IP>/api/health
```

### ログ

```bash
docker compose logs --tail=50 external-firewall
docker compose logs --tail=50 reverse-proxy
```

## 結果の見方

- `nc succeeded`
  - そのポートに到達でき、待受もある
- `Connection refused`
  - host には届いたが、そのポートで待受していない
- `timeout`
  - packet filtering や経路上の drop を疑いやすい

## 今回の比較で言えること

- `external-firewall` コンテナの役割
  - Docker 上で公開入口を 1 か所に集約すること
  - `reverse-proxy` を host 直公開から外すこと
- `nftables` の役割
  - host 側で packet filtering を補助すること
  - ただし Docker 公開経路の主実装ではないこと

この 2 つを分けて比較することで、「入口分離」と「packet filtering 補助」が別の役割であることを説明できます。

## 実測した比較例

今回の検証では、`external-firewall` を停止した状態と、強制作り直し後の状態を比較しました。

### 1. `external-firewall` 停止時

実行:

```bash
docker compose stop external-firewall
```

観察できたこと:

- `reverse-proxy`, `internal-firewall`, `fastapi-app`, `postgres` は起動したままだった
- それでも外部公開の最前段が止まるため、`80/443` は利用できなくなった
- これは `reverse-proxy` を host に直接公開していない設計と整合する

この結果から言えること:

- `external-firewall` は単なる補助コンテナではなく、公開入口そのものを担っている
- 内部側が動いていても、外周入口が止まれば外部疎通は成立しない

### 2. `external-firewall` 再作成後

実行:

```bash
docker compose up -d --force-recreate external-firewall
```

確認結果:

- `external-firewall` は `up`
- `127.0.0.1:80` は `open`
- `127.0.0.1:443` は `open`
- `http://127.0.0.1/health` は `200 OK`
- `http://127.0.0.1/api/health` は `200 OK`
- `https://127.0.0.1/api/health` は `200 OK`

この結果から言えること:

- `external-firewall` の復旧により、外部入口が回復した
- `External Firewall -> Reverse Proxy -> Application -> Backend -> Postgres` の本線が成立した
- HTTP と HTTPS の両方で、入口から FastAPI までの疎通を確認できた

## 報告書向けのまとめ

`external-firewall` 停止時には、内部コンテナが起動中であっても `80/443` の公開入口が失われ、外部疎通は成立しなかった。  
一方、`external-firewall` を再作成した後は `80/443` が再び開き、`/health` および `/api/health` が `200 OK` を返した。  
この比較により、`external-firewall` が本構成における公開入口を一元化し、実際に External Firewall 相当の役割を担っていることを確認できた。

## `nftables` 実験の結果

今回の host 側補助ポリシー実験では、次のルールを適用しました。

```bash
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
```

確認できた table:

```text
table inet codex_external_fw1 {
    chain input {
        type filter hook input priority filter; policy drop;
        iif "lo" accept
        ct state established,related accept
        ip protocol icmp accept
        ip6 nexthdr ipv6-icmp accept
        tcp dport { 22, 80, 443 } accept
    }
}
```

### 適用前 baseline

- `192.168.64.4:80`
  - succeeded
- `192.168.64.4:443`
  - succeeded
- `192.168.64.4:3001`
  - `Connection refused`
- `http://192.168.64.4/health`
  - `200 OK`
- `https://192.168.64.4/api/health`
  - `200 OK`

### 適用後

- `192.168.64.4:80`
  - succeeded
- `192.168.64.4:443`
  - succeeded
- `192.168.64.4:3001`
  - `Connection refused`
- `http://192.168.64.4/health`
  - `200 OK`
- `https://192.168.64.4/api/health`
  - `200 OK`

### この結果が意味すること

- `nftables` 適用後も、許可した `22/80/443` の正常疎通は維持できた
- 一方、`3001` は `timeout` ではなく `Connection refused` のままだった
- したがって、今回の `input chain` ルールだけでは Docker 公開ポート経路に対する遮断効果を明確には観測できなかった

### ここから説明できること

この結果は、README で整理した設計意図と一致しています。

- `external-firewall` コンテナ
  - Docker 上の外周入口分離を担う主実装
- `nftables`
  - host 側の packet filtering 補助
  - ただし、Docker の publish port が forwarding / NAT を伴うため、`input` chain だけでは Docker 公開経路全体を完全統制できるとは限らない

報告書向けには、次のように書けます。

`nftables` による host `input` chain への許可ルール適用後も、`80/443` の正常疎通は維持された。  
一方で、非許可想定の `3001` は `timeout` ではなく `Connection refused` であり、今回の `input chain` ルールだけでは Docker 公開ポート経路に対する遮断効果を明確には確認できなかった。  
この結果から、Docker 上の External Firewall 実装では、`nginx stream` による入口分離が主実装であり、`nftables` は host 側補助ポリシーとして位置づけるのが妥当である。

## ここまでの検証結果まとめ

今回の External Firewall 検証では、次の観点を順に確認しました。

### 1. 公開入口の分離

確認したこと:

- host に公開される `80/443` の入口を `external-firewall` に集約した
- `reverse-proxy` は host に直接公開しない構成にした

確認方法:

- `docker compose ps`
- `docker compose stop external-firewall`
- `docker compose up -d --force-recreate external-firewall`

確認できたこと:

- `external-firewall` 停止時は、内部コンテナが動いていても外部疎通は成立しなかった
- `external-firewall` 復旧後は `80/443` が再び利用可能になった

### 2. HTTP / HTTPS の本線疎通

確認したこと:

- `External Firewall -> Reverse Proxy -> Application -> Backend -> Postgres` の本線が成立しているか

確認方法:

- `curl -i http://127.0.0.1/health`
- `curl -i http://127.0.0.1/api/health`
- `curl -k -i https://127.0.0.1/api/health`
- `curl -i http://192.168.64.4/health`
- `curl -k -i https://192.168.64.4/api/health`

確認できたこと:

- HTTP `200 OK`
- HTTPS `200 OK`
- FastAPI の health JSON も正常応答

### 3. L4 到達性

確認したこと:

- 許可対象の `80/443` と、非許可想定ポートの応答差

確認方法:

- `nc -vz -w 3 192.168.64.4 80`
- `nc -vz -w 3 192.168.64.4 443`
- `nc -vz -w 3 192.168.64.4 3001`

確認できたこと:

- `80/443` は succeeded
- `3001` は `Connection refused`

### 4. `nftables` 補助ポリシー

確認したこと:

- host `input` chain に `22/80/443` を許可した場合の動作

確認方法:

- `FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh`
- `./external-firewall/show-nft.sh`
- 適用前後で同じ `nc` / `curl` を比較

確認できたこと:

- `80/443` の正常疎通は維持された
- `3001` は `Connection refused` のままで、`timeout` には変化しなかった

### 5. 総合解釈

今回の結果から、次のように整理できます。

- `external-firewall` コンテナ
  - Docker 上の主実装
  - 公開入口を 1 か所に固定し、DMZ の `reverse-proxy` を host 直公開から外す
- `nftables`
  - host 側の補助実装
  - packet filtering の考え方そのものは再現できる
  - ただし `input chain` だけで Docker 公開ポート経路全体を明確に制御できるとは限らない

## 報告書向けの総括

今回の External Firewall 実装では、Docker 上で `external-firewall` コンテナを公開入口として分離し、その後段に DMZ の `reverse-proxy` を配置することで、外周サーバーと公開サーバーの責務分離を擬似再現した。  
実際に `external-firewall` を停止すると外部疎通は失われ、再作成後には `80/443` の到達性と HTTP/HTTPS の正常応答が回復したことから、このコンテナが本構成における公開入口を担っていることを確認できた。  
また、`nftables` により host `input` chain` へ `22/80/443` の許可ルールを適用したが、非許可想定の `3001` は `Connection refused` のままであり、今回の `input chain` ルールのみでは Docker 公開ポート経路に対する遮断効果を明確には観測できなかった。  
この結果から、現段階の External Firewall は `nginx stream` による入口分離を主実装とし、`nftables` は host 側補助ポリシーとして位置づけるのが妥当である。

### 注意

`apply-nft.sh` は host の `input` policy を `drop` にします。  
リモート接続中に使う場合は、必要な管理ポートを自分で明示してから適用してください。

# External Firewall Layer

このディレクトリには、`waf` より手前に置く `external-firewall` の設定を置いています。

## 役割

- 外部から入る `80/443` の TCP 接続を最初に受ける
- 送信元は `Any` とし、宛先は `80/443` だけを後段の `waf` に転送する
- HTTP レイヤの検査は行わず、L3/L4 ホワイトリスト型の外周境界として振る舞う

## 現在の実装

- カーネル層
  - `nftables` による host input policy
  - `80/443` 以外を先に落とすための companion 実装
- ユーザ空間
  - Nginx `stream` による TCP プロキシ
- `80` は `waf:80` に転送
- `443` は `waf:443` に転送
- 送信元 IP による制限はかけない
- 宛先ポートは `80` と `443` だけを公開する
- `80/443` 以外は listen しないため、FW1 のポリシーとしてはデフォルト拒否になる

## 入口の流れ

いまの FW1 は 1 つの仕組みではなく、次の 2 層で成り立っています。

1. Ubuntu / Linux カーネル
   - 必要なら `nftables` が先に `80/443` 以外を落とす
2. Docker の公開ポート
   - `80/443` は `external-firewall` コンテナに渡る
3. `external-firewall`
   - `nginx stream` で `waf` に TCP 転送する

つまり、今の入口は次の流れです。

```text
Client
  -> Linux kernel / nftables
  -> Docker published port 80/443
  -> external-firewall (nginx stream)
  -> waf
```

## ポリシー整理

- L3 観点
  - 送信元は `Any`
  - 宛先はこの VM の公開先として `80/443` のみを対象にする
- L4 観点
  - TCP `80` を `waf:80` に転送
  - TCP `443` を `waf:443` に転送
- デフォルト動作
  - `80/443` 以外の宛先ポートは受けない
  - したがって、外周ポリシーとしては「宛先ホワイトリスト型、その他拒否」

## カーネル層の実装

FW1 をカーネル段階でも効かせたい場合は、同じディレクトリの `nftables` 補助スクリプトを使います。

- `apply-nft.sh`
  - host の `input` chain に `80/443` ホワイトリストを入れる
- `show-nft.sh`
  - 現在の FW1 table を表示する
- `remove-nft.sh`
  - FW1 table を削除する

デフォルトの許可ポート:

- `80`
- `443`

必要なら環境変数で上書きできます。

```bash
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
```

通常適用:

```bash
./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
```

削除:

```bash
./external-firewall/remove-nft.sh
```

## 検証前提

この repo の確認メモでは、Linux VM の IP は次を使っています。

```text
192.168.64.4
```

Mac から検証する際は、以下の `VM_IP` を必要に応じて読み替えてください。

```bash
VM_IP=192.168.64.4
```

VM 側で IP を再確認したい場合:

```bash
hostname -I
ip -4 addr show
```

## FW1 を有効化する

### 注意

`apply-nft.sh` は host の `input` policy を `drop` にします。  
そのため、SSH を使っている場合は `22` を許可ポートに含めたまま適用してください。

### SSH を維持したまま有効化する例

```bash
cd ~/infra
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
```

### HTTP/HTTPS だけに絞る例

```bash
cd ~/infra
./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
```

この場合、`22` も遮断されます。

## FW1 を無効化する

```bash
cd ~/infra
./external-firewall/remove-nft.sh
```

これは `nftables` だけを外します。  
`external-firewall` コンテナ自体は止めないため、`80/443` の入口は残ります。

## `external-firewall` コンテナだけを止める

```bash
cd ~/infra
docker compose stop external-firewall
```

再開:

```bash
cd ~/infra
docker compose start external-firewall
```

これは `nginx stream` 側の FW1 を止めます。  
今の構成では `80/443` を host で公開しているのは `external-firewall` だけなので、これを止めると外部からのアクセスはできなくなります。  
無防備になるのではなく、入口そのものが消えます。

## FW1 を完全に無効化する

```bash
cd ~/infra
./external-firewall/remove-nft.sh
docker compose stop external-firewall
```

この場合:

- `nftables` のカーネル層ポリシーは外れる
- `nginx stream` の入口も止まる
- 結果として、今の構成では外部から `80/443` へはアクセスできない

## 典型的な検証手順

1. VM 側でサービスを起動する

```bash
cd ~/infra
docker compose up -d
```

2. FW1 を無効状態にする

```bash
cd ~/infra
./external-firewall/remove-nft.sh
```

3. Mac から baseline を確認する
4. FW1 を有効化する
5. 同じコマンドをもう一度打って差分を見る

## Mac からの確認コマンド

### L4 到達性確認

```bash
VM_IP=192.168.64.4

nc -vz $VM_IP 80
nc -vz $VM_IP 443
nc -vz -w 3 $VM_IP 3001
```

### HTTP/HTTPS 確認

```bash
VM_IP=192.168.64.4

curl -i http://$VM_IP/health
curl -i http://$VM_IP/api/health
curl -i http://$VM_IP/api/info
curl -k -i https://$VM_IP/api/health
```

### WAF の簡易確認

```bash
VM_IP=192.168.64.4

curl -i -A "sqlmap" http://$VM_IP/
curl -i "http://$VM_IP/?q=<script>"
curl -i -X PUT http://$VM_IP/
```

期待値:

- 危険な User-Agent は `403`
- 不審な query は `403`
- 非許可メソッドは `405`

## 結果の見方

### `nc` の見方

- `succeeded`
  - 到達していて、そのポートで待受もある
- `Connection refused`
  - host には届いているが、そのポートで待受していない
- `timeout`
  - `nftables` などで drop されている可能性が高い

### 今回特に見たい差分

- FW1 無効時
  - `80`, `443` は成功する想定
  - `3001` は `refused` または環境によっては別の失敗になる
- FW1 有効時
  - `80`, `443` は成功
  - `3001` は `timeout` になれば、L3/L4 段階で落とせていると判断しやすい

## 一連の検証例

### 1. baseline を取る

VM 側:

```bash
cd ~/infra
docker compose up -d
./external-firewall/remove-nft.sh
```

Mac 側:

```bash
VM_IP=192.168.64.4

nc -vz $VM_IP 80
nc -vz $VM_IP 443
nc -vz -w 3 $VM_IP 3001

curl -i http://$VM_IP/api/health
curl -k -i https://$VM_IP/api/health
```

### 2. FW1 を有効化する

VM 側:

```bash
cd ~/infra
FW1_ALLOWED_TCP_PORTS="22,80,443" ./external-firewall/apply-nft.sh
./external-firewall/show-nft.sh
```

### 3. 同じ確認をもう一度行う

Mac 側:

```bash
VM_IP=192.168.64.4

nc -vz $VM_IP 80
nc -vz $VM_IP 443
nc -vz -w 3 $VM_IP 3001

curl -i http://$VM_IP/api/health
curl -k -i https://$VM_IP/api/health
curl -i -A "sqlmap" http://$VM_IP/
```

## 今回の結果

- `nftables` を使った FW1 の companion 実装を追加した
- 追加したスクリプトは `apply-nft.sh`, `show-nft.sh`, `remove-nft.sh`
- これにより、`nginx stream` の前段で host の `input` chain に `80/443` ホワイトリストを入れられる
- 現時点ではスクリプト追加と Bash 構文確認まで完了している
- host への実適用と Mac からの到達確認は、まだこの README の手順を実行していない

## 注意

- これはクラウド WAF やネットワーク機器の完全代替ではなく、ローカル検証用の外周境界です
- 拒否時の見え方は環境依存で、`timeout` ではなく接続拒否に見える場合があります
- 純粋なカーネルパケットフィルタではなく、`nginx stream` による L4 境界です
- `apply-nft.sh` は host の `input` policy を `drop` にするため、`FW1_ALLOWED_TCP_PORTS` を `80,443` のまま適用すると `22` なども遮断します
- SSH を維持したい環境では `FW1_ALLOWED_TCP_PORTS="22,80,443"` のように管理ポートを明示的に含めてください

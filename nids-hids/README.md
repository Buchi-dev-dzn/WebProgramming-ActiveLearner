# NIDS / NIPS / HIDS Notes

このディレクトリは、Docker Compose の直列コンポーネントではない防御層を整理するためのメモです。

## 配置イメージ

- `NIDS/NIPS`
  - Linux VM の NIC もしくはホストネットワーク境界で通信を監視する
  - `public_net` に入る前後のトラフィックを観測対象にする
- `HIDS`
  - Linux VM 自体、またはコンテナホスト上でプロセス、ファイル変更、認証イベントを監視する

## このリポジトリでの扱い

- `NIDS/NIPS` は compose の直列サービスとしては置かない
- `HIDS` も backend の前段に入れるのではなく、ホスト監視として別レイヤーで扱う
- README では、通信経路の本線と監視系を分けて記述する

## 将来候補

- `Suricata` を host network 側で導入
- `Wazuh agent` を VM に導入
- `Loki` や `ELK` にログ転送

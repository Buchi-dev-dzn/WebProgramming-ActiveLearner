#!/usr/bin/env bash
set -euo pipefail

table_name="${FW1_TABLE_NAME:-codex_external_fw1}"
allowed_tcp_ports="${FW1_ALLOWED_TCP_PORTS:-80,443}"

sudo nft delete table inet "$table_name" >/dev/null 2>&1 || true

sudo nft -f - <<EOF
table inet $table_name {
  chain input {
    type filter hook input priority 0; policy drop;

    iif "lo" accept
    ct state established,related accept
    ip protocol icmp accept
    ip6 nexthdr ipv6-icmp accept
    tcp dport { $allowed_tcp_ports } accept
  }
}
EOF

echo "applied FW1 kernel policy: table=$table_name allowed_tcp_ports={$allowed_tcp_ports}"

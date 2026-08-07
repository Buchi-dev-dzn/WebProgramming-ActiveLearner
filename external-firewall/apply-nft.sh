#!/usr/bin/env bash
set -euo pipefail

table_name="${FW1_TABLE_NAME:-codex_external_fw1}"
allowed_tcp_ports="${FW1_ALLOWED_TCP_PORTS:-22,443}"
external_ifaces="${FW1_EXTERNAL_IFACES:-}"

if [ -z "$external_ifaces" ]; then
    external_ifaces="$(ip -o route show default | awk 'NR == 1 {print $5}')"
fi

if [ -z "$external_ifaces" ]; then
    echo "Could not determine the external interface. Set FW1_EXTERNAL_IFACES." >&2
    exit 1
fi

sudo nft delete table inet "$table_name" >/dev/null 2>&1 || true

# Docker-published traffic is routed through the host forward hook after
# DNAT, so the forward chain is required in addition to the host input chain.
# FW1_EXTERNAL_IFACES is a comma-separated nftables interface set.
sudo nft -f - <<EOF
table inet $table_name {
  chain input {
    type filter hook input priority 0; policy drop;

    iif "lo" accept
    ct state established,related accept
    ip protocol icmp accept
    ip6 nexthdr ipv6-icmp accept
    iifname { $external_ifaces } tcp dport { $allowed_tcp_ports } accept
    iifname { $external_ifaces } drop
  }

  chain forward {
    type filter hook forward priority 0; policy accept;

    ct state established,related accept
    iifname { $external_ifaces } tcp dport { $allowed_tcp_ports } accept
    iifname { $external_ifaces } drop
  }
}
EOF

echo "applied FW1 kernel policy: table=$table_name external_ifaces={$external_ifaces} allowed_tcp_ports={$allowed_tcp_ports}"

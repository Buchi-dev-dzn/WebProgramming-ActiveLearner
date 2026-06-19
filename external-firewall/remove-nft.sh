#!/usr/bin/env bash
set -euo pipefail

table_name="${FW1_TABLE_NAME:-codex_external_fw1}"

sudo nft delete table inet "$table_name"
echo "removed FW1 kernel policy: table=$table_name"

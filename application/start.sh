#!/bin/sh
set -eu

mkdir -p /run/nginx /var/log/application

node /app/backend/server.js &

exec nginx -g 'daemon off;'

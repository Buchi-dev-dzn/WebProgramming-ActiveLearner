#!/bin/sh
set -eu

: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD must be set}"

role_exists="$(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -Atc "SELECT 1 FROM pg_roles WHERE rolname = 'app_user'")"
if [ "$role_exists" = "1" ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        --set=app_password="$POSTGRES_APP_PASSWORD" \
        -c "ALTER ROLE app_user WITH LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE"
else
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        --set=app_password="$POSTGRES_APP_PASSWORD" \
        -c "CREATE ROLE app_user LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE"
fi

#!/usr/bin/env bash
# PostgreSQL's entrypoint executes or sources this file only for an empty volume.
(
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=a_password="$LAB_A_PASSWORD" --set=b_password="$LAB_B_PASSWORD" <<'SQL'
CREATE ROLE lab_a_reader LOGIN PASSWORD :'a_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE lab_b_reader LOGIN PASSWORD :'b_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE lab_a_reader SET statement_timeout = '5s';
ALTER ROLE lab_b_reader SET statement_timeout = '5s';
ALTER ROLE lab_a_reader SET default_transaction_read_only = on;
ALTER ROLE lab_b_reader SET default_transaction_read_only = on;
SQL
)

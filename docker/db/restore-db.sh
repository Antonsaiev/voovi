#!/usr/bin/env sh
set -eu

: "${MYSQL_HOST:=db}"
: "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
: "${MYSQL_DATABASE:=voovi}"
: "${MYSQL_USER:=voovi.ru}"
: "${MYSQL_PASSWORD:=voovi.ru}"
: "${MYSQL_DUMP_FILE:=/dump/voovi.sql}"

if [ ! -s "$MYSQL_DUMP_FILE" ]; then
  echo "SQL dump not found or empty: $MYSQL_DUMP_FILE" >&2
  exit 1
fi

echo "Recreating database '$MYSQL_DATABASE' on '$MYSQL_HOST' from $MYSQL_DUMP_FILE"

mysql -h"$MYSQL_HOST" -uroot -p"$MYSQL_ROOT_PASSWORD" <<SQL
DROP DATABASE IF EXISTS \`$MYSQL_DATABASE\`;
CREATE DATABASE \`$MYSQL_DATABASE\` CHARACTER SET utf8 COLLATE utf8_general_ci;
GRANT ALL PRIVILEGES ON \`$MYSQL_DATABASE\`.* TO '$MYSQL_USER'@'%' IDENTIFIED BY '$MYSQL_PASSWORD';
FLUSH PRIVILEGES;
SQL

mysql -h"$MYSQL_HOST" -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < "$MYSQL_DUMP_FILE"

if [ -s /dump/avkontur_bill_link.sql ]; then
  needs_bill_link=$(mysql -h"$MYSQL_HOST" -uroot -p"$MYSQL_ROOT_PASSWORD" -N "$MYSQL_DATABASE"     -e "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='avkontur' AND COLUMN_NAME='rand_schet'")
  if [ "$needs_bill_link" = 1 ]; then
    mysql -h"$MYSQL_HOST" -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" < /dump/avkontur_bill_link.sql
  fi
fi

echo "Database '$MYSQL_DATABASE' restored"

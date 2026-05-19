#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

compose=(docker compose --env-file configs/env/local.env -f docker-compose.yml)

ensure_runtime_dirs() {
  mkdir -p \
    voovi.ru/log \
    voovi.ru/doc \
    voovi.ru/upload \
    voovi.ru/files \
    voovi.ru/voicecatalog \
    voovi.ru/img \
    voovi.ru/vipiska \
    voovi.ru/scheta \
    voovi.ru/mail/database
}

restore_db() {
  "${compose[@]}" up -d --build db
  "${compose[@]}" run --rm db-restore
}

up_with_restore() {
  ensure_runtime_dirs
  restore_db
  "${compose[@]}" up -d --build --remove-orphans --no-deps \
    voovi-web \
    svoovi-api \
    svoovi-api-tasks \
    analitic \
    phpmyadmin
  "${compose[@]}" up -d --build --remove-orphans --no-deps nginx
}

case "${1:-up}" in
  up)
    up_with_restore
    ;;
  start)
    ensure_runtime_dirs
    "${compose[@]}" up -d --build db
    "${compose[@]}" up -d --build --remove-orphans --no-deps \
      voovi-web \
      svoovi-api \
      svoovi-api-tasks \
      analitic \
      phpmyadmin
    "${compose[@]}" up -d --build --remove-orphans --no-deps nginx
    ;;
  restore-db)
    restore_db
    ;;
  stop)
    "${compose[@]}" stop
    ;;
  down)
    "${compose[@]}" down
    ;;
  logs)
    shift
    "${compose[@]}" logs -f "$@"
    ;;
  help|-h|--help)
    cat <<'HELP'
Usage:
  ./dev.sh             build, restore voovi.sql, and start the full dev stack
  ./dev.sh up          same as above; database is dropped and restored
  ./dev.sh start       start stack without restoring the database
  ./dev.sh restore-db  drop and restore only the local database from voovi.sql
  ./dev.sh logs [svc]  follow compose logs
  ./dev.sh stop        stop containers
  ./dev.sh down        remove containers and network, keep volumes
HELP
    ;;
  *)
    exec "${compose[@]}" "$@"
    ;;
esac

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
    voovi.ru/mail/database \
    var/bank/Downloads \
    var/bank/file_for_process \
    analitic/uploads/payments
}

restore_db() {
  "${compose[@]}" stop voovi-web svoovi-api svoovi-api-tasks analitic
  "${compose[@]}" up -d --build --wait --wait-timeout 180 db
  "${compose[@]}" run --rm db-restore
}

case "${1:-start}" in
  start|up)
    ensure_runtime_dirs
    "${compose[@]}" up -d --build --wait --wait-timeout 180 db
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
  ./dev.sh             start without restoring the database
  ./dev.sh up          same as start
  ./dev.sh start       start the stack with the existing database
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

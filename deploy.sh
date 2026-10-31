#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose=(docker compose --env-file configs/env/prod.env -f docker-compose.prod.yml)
"${compose[@]}" build
"${compose[@]}" up -d --no-build

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose=(docker compose --env-file configs/env/prod.env -f docker-compose.prod.yml)
# Проверяем реальные банковские источники до обновления работающих контейнеров.
# При выключенном банке новые пустые каталоги не блокируют общий worker.
"${compose[@]}" config --format json | python3 -c '
import json, pathlib, sys
worker = json.load(sys.stdin)["services"]["svoovi-api-tasks"]
enabled = str(worker.get("environment", {}).get("BANK_AUTOMATION_ENABLED", "0")).lower() in ("1", "true")
if enabled:
    missing = [v["source"] for v in worker.get("volumes", [])
               if v.get("type") == "bind" and v["target"] in
               ("/usr/src/app/var/banking/incoming", "/usr/src/app/var/banking/prepared")
               and not pathlib.Path(v["source"]).is_dir()]
    if missing:
        sys.exit("Нет папок получения банковских файлов: " + ", ".join(missing))
'
mkdir -p analitic/uploads/payments
"${compose[@]}" build
"${compose[@]}" up -d --no-build

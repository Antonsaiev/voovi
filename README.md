# voovi

Основной репозиторий-обвязка для локального и production-запуска связки:

- `voovi.ru` - legacy PHP-приложение;
- `s.voovi.ru-api` - Django API;
- `analitic` - Django аналитика;
- `db` - MariaDB/MySQL-compatible база.

Код приложений подключен как git submodules.

## Первый запуск

```bash
git submodule update --init --recursive
./dev.sh
```

`./dev.sh` всегда полностью пересоздает локальную базу `MYSQL_DATABASE` из `voovi.sql`: выполняет `DROP DATABASE`, создает базу заново и импортирует dump. Бэкап предыдущей локальной базы не делается.

Адреса по умолчанию:

- `voovi.ru` через nginx: http://voovi.localhost
- `s.voovi.ru-api` через nginx: http://s.voovi.localhost
- `analitic` через nginx: http://analitic.voovi.localhost
- `voovi.ru` напрямую: http://localhost:8080
- `s.voovi.ru-api` напрямую: http://localhost:7005
- `analitic` напрямую: http://localhost:7000
- phpMyAdmin: http://localhost:8081
- MySQL на host-машине: `127.0.0.1:3308`

Домены `*.localhost` обычно резолвятся в `127.0.0.1` без правки `/etc/hosts`. Если в вашем окружении это не работает, добавьте `voovi.localhost`, `s.voovi.localhost` и `analitic.voovi.localhost` в hosts-файл.

Для общей PHP-авторизации локально заходите через nginx-домены `*.voovi.localhost`, а не через прямые `localhost`-порты. Cookie авторизации ставится на домен `voovi.localhost` и отправляется браузером также на `s.voovi.localhost`.

## Локальная разработка

```bash
./dev.sh             # build + restore voovi.sql + старт всей связки
./dev.sh start       # старт без restore базы
./dev.sh restore-db  # только полный restore базы из voovi.sql
./dev.sh logs        # логи всех сервисов
./dev.sh down        # удалить контейнеры и сеть, volume с БД оставить
```

Env-файлы лежат в `configs/env/`: `local.env` для локальной разработки и `prod.env` для production. `voovi.sql` не хранится в git. Файл должен лежать в корне репозитория рядом с `docker-compose.yml`.

## Production

Перед запуском production замените placeholder-значения в `configs/env/prod.env` на реальные пароли и секреты, затем:

```bash
docker compose --env-file configs/env/prod.env -f docker-compose.prod.yml up -d --build
```

Production-compose не импортирует `voovi.sql` автоматически и не поднимает phpMyAdmin. База хранится в Docker volume `voovi_mysql`; публичный HTTPS лучше закрывать внешним reverse proxy, который проксирует в root nginx на `127.0.0.1:8080`.

## Submodules

```bash
git submodule update --init --recursive
git submodule foreach git status --short --branch
```

Для обновления конкретного проекта:

```bash
cd s.voovi.ru-api
git pull --ff-only
cd ..
git add s.voovi.ru-api
```

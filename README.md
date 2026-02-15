# TVPN Backend

Backend для Telegram-бота и WebApp. Поднимает HTTP API (FastAPI), админку FastAdmin,
фоновые задачи и интеграции с RemnaWave и YooKassa.

## Требования

- Python 3.11+
- Poetry
- Docker (для Postgres и/или полного запуска)

## Быстрый старт (локально)

1. Установить зависимости:
   ```bash
   cd Blubcat_BACK_END
   poetry install
   ```
2. Подготовить окружение:
   ```bash
   cp .env.example .env
   ```
   Заполните значения переменных (минимум: Telegram, RemnaWave, DB, админ-аккаунт).
3. Поднять базу:
   ```bash
   docker compose up -d bloobcat_db
   ```
4. Запустить приложение:
   ```bash
   poetry run python -m bloobcat
   ```

Проверка: `http://localhost:33083/health`

## Запуск через Docker Compose (приложение + БД)

```bash
cd Blubcat_BACK_END
docker compose up -d --build
```

- API: `http://localhost:33083`
- Логи приложения: `Blubcat_BACK_END/logs/`
- `pgadmin` в compose без публикации порта (добавьте `ports`, если нужно).

## Переменные окружения

Полный список смотрите в `.env.example`. Критичные для старта:

- `TELEGRAM_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_MINIAPP_URL`, `TELEGRAM_WEBAPP_URL`
- `REMNAWAVE_URL`, `REMNAWAVE_TOKEN`
- `SCRIPT_DB` (строка подключения к Postgres)
- `SCRIPT_API_URL` (используется для webhook URL)
- `ADMIN_TELEGRAM_ID`, `ADMIN_LOGIN`, `ADMIN_PASSWORD`

Дополнительно для Captain User Lookup:

- `API_KEY` и `ALLOWLIST_DOMAINS` (пример в `.env.captain-user-lookup.example`)

## Миграции

Миграции применяются автоматически при старте приложения. Если нужно вручную:

```bash
poetry run aerich upgrade
```

## Диагностика роста БД

Для безопасной проверки размера БД и потенциально "тяжёлых" таблиц:

```bash
poetry run python scripts/db_growth_report.py
```

Настраиваемые пороги через env:
- `DB_GROWTH_WARN_MB` (по умолчанию `4096`)
- `DB_GROWTH_TOP_N` (по умолчанию `30`)

Отправка алерта в админ-чат (использует настройки бота из `.env`):

```bash
poetry run python scripts/db_growth_notify.py --warn-mb 4096 --top-n 30 --top-lines 8
```

- По умолчанию отправляет сообщение только при warning.
- Для принудительной отправки используйте `--send-always`.

## TESTMODE

`TESTMODE=TRUE` включает подготовку тестовых данных при старте (тарифы, промо и т.д.).

## Основные эндпоинты

- `GET /health` — healthcheck
- `GET /admin` — админка FastAdmin
- `POST /webhook/<TELEGRAM_WEBHOOK_SECRET>` — webhook Telegram
- `GET /api/users/{telegram_id}` — Captain User Lookup (Bearer `API_KEY`)

## Frontend (если нужен локально)

```bash
cd Blubcat_FRONT_END
npm install
npm run dev
```

Не забудьте выставить `TELEGRAM_WEBAPP_URL` и `TELEGRAM_MINIAPP_URL` на адрес фронтенда.

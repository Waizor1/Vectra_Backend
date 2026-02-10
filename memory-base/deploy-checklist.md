# Deploy checklist (TVPN_BACK_END)

Цель: выкатить бэкенд без регрессий, с применением миграций и проверкой реферальной системы (дни, не ₽).

## Перед сборкой/деплоем (локально)

1) Автопроверки:

- `py -3.12 -m pytest`
- `py -3.12 -m compileall bloobcat`

2) Docker build (с build time):

- PowerShell:
  - `$env:BUILD_TIME = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")`
  - `docker compose -f docker-compose.yml build bloobcat`

## Миграции

Важно: миграции применяются автоматически при старте приложения (см. `bloobcat/__main__.py`, lifespan).

Если нужно применить миграции отдельно (fail-fast в deploy pipeline):

- `py -3.12 scripts/apply_migrations.py`

Скрипт повторяет логику: `init -> upgrade`, и при отсутствии таблицы `aerich` делает `init_db(safe=True)` и повторяет `upgrade`.

## После деплоя (смоук)

1) Проверить health:
- `GET /health` (должен вернуть `status=ok`, `version`, `build_time`)

2) Рефералка:
- `GET /referrals/status` (под авторизацией Telegram WebApp initData/Bearer)
  - `totalBonusDays` — это **накопленные бонусные дни** (не баланс)
  - `friendsCount` — количество приглашённых (зарегистрированных)

3) Покупка по ссылке друга (проверка начислений):
- Друг: +7 дней только при **первой оплате** (1 раз)
- Реферер: +7/+20/+36/+60/+120 (зависит от покупки друга)
- Семейная подписка (10 устройств) не продлевается бонусами (дни копятся в счётчике)


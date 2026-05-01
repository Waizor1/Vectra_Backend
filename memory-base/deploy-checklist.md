# Deploy checklist (Vectra_Backend)

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

## Перед полноценным запуском RemnaWave/VPN-доступа

- Временно для web OAuth регистраций (`Google`/`Yandex`) trial выдаётся локально до создания профиля в RemnaWave, а `Users._ensure_remnawave_user()` уходит в background, чтобы не держать экран "Быстрый вход" 60 секунд при отсутствующей Vectra RemnaWave панели.
- Welcome-agent больше не используется: `/welcome-vpn` возвращает `featureEnabled=false` и не обращается к RemnaWave. При подключении стабильной подписки/панели для Vectra Connect **не создавать и не активировать** пользователя `welcome-agent`; первичный вход идёт через браузер.
- Когда Vectra RemnaWave будет настроен (`REMNAWAVE_URL`, token, internal/external squad UUIDs, DNS/health), вернуть production-safe поведение: регистрация/выдача demo должна подтверждать создание/привязку RemnaWave профиля, а дата trial должна синхронизироваться в RemnaWave до того, как пользователь увидит готовый ключ.
- Обязательный smoke перед запуском: новый Google/Yandex web-user -> `/auth/complete-registration` быстро завершается -> у пользователя есть `expired_at/is_trial/used_trial` -> `remnawave_uuid` создан -> `/user` возвращает `subscription_url_status=ready` и не показывает `account_initializing`.

## Ручной backfill (если нужно исправить пропущенное начисление)

Если оплата прошла, но реферальные дни не начислились (например, исторически `referred_by` не был проставлен),
можно безопасно прогнать скрипт (идемпотентность обеспечивается ledger-таблицей `referral_rewards`):

- `py -3.12 scripts/backfill_referral_reward.py --referred-user-id <ID_друга> --payment-id <yookassa_payment_id> --months <M> --device-count <D> --amount-rub <RUB> --set-referrer-id <ID_реферера> --notify`

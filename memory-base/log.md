## 2026-02-20

- **fix(db/users-delete-fk-guard):** устранен повторяющийся `INTERNAL_SERVER_ERROR` при удалении пользователя из-за дрейфа FK `active_tariffs -> users`.
  - **Симптом:** удаление `users` падало с `violates foreign key constraint "fk_active_tariffs_user"` (в `active_tariffs` было `NO ACTION/RESTRICT` вместо `CASCADE`).
  - **RCA:** проверка self-heal в `bloobcat/__main__.py` была завязана на фиксированное имя constraint (`fk_active_tariffs_user`) и могла пропустить drift при другом имени FK.
  - **Исправление:**
    - добавлен модуль `bloobcat/db/fk_guards.py` с устойчивой проверкой FK по структуре связи (`active_tariffs.user_id -> users.id`), а не по имени;
    - `bloobcat/__main__.py` переведен на импорт guard-функций из `fk_guards`;
    - в `bloobcat/db/users.py::Users.delete()` добавлен runtime fail-safe `await ensure_active_tariffs_fk_cascade()` перед `super().delete()`.
  - **Ops-диагностика:** `directus/extensions/endpoints/server-ops/index.js` команда `fk_active_tariffs` теперь также проверяет FK по таблице/колонке и показывает фактические constraints + `delete_rule`.
  - Дополнительно исправлен SQL в `fk_users_overview` (PostgreSQL-совместимый join через `constraint_column_usage`), чтобы команда не падала при диагностике.
  - **Тесты:** в `tests/test_resilience_hardening.py` добавлены регрессии:
    - self-heal применяет repair при `NO ACTION`;
    - `Users.delete()` вызывает FK guard перед удалением.
  - Прогон: `py -3.12 -m pytest tests/test_resilience_hardening.py -q` -> `11 passed`.

- **feat(deploy/tariffs):** автосидирование тарифов добавлено в backend auto-deploy.
  - Добавлен новый идемпотентный скрипт `scripts/seed_tariffs.py`:
    - source-of-truth по дефолтным тарифам теперь хранится в `TARIFFS` (редактирование/добавление позиций в одном месте);
    - режим изменен на `insert-only` по логическому ключу `name + months` (без `TRUNCATE`);
    - существующие тарифы не обновляются и не удаляются при деплое (ручные правки в админке не затираются).
  - Обновлён workflow `TVPN_BACK_END/.github/workflows/auto-deploy.yml`:
    - после шага `directus_super_setup` запускается `python scripts/seed_tariffs.py` внутри контейнера `bloobcat`;
    - добавлен retry (`3` попытки, пауза `5s`);
    - при неуспехе seed деплой завершается с ошибкой (`exit 1`), чтобы не выкатывать backend без актуальных тарифов.
  - Операционный эффект: для обновления дефолтных тарифов достаточно править `scripts/seed_tariffs.py`; при каждом деплое изменения автоматически применяются.

- **fix(remnawave/checkers):** устранена регрессия чекеров активации, подключений и HWID — восстановлена синхронизация с Directus и отправка в лог-канал.
  - **Симптом:** после миграции с FastAdmin на Directus перестали работать чекеры активации устройств, количества подключений и одинаковых HWID; значения не обновлялись в Directus (админ-панель), уведомления не приходили в лог-чат.
  - **Корневая причина (баг `continue`):**
    - В `bloobcat/routes/remnawave/catcher.py` на строке ~392 при неудачном парсинге `onlineAt` через `_safe_parse_online_at()` выполнялся `continue`, который пропускал **ВСЮ** оставшуюся обработку пользователя, включая:
      - синхронизацию `expired_at` (БД → RemnaWave),
      - синхронизацию `hwid_limit` (двустороннюю),
      - отправку обновлений в RemnaWave.
    - В эталонном бэкенде при ошибке парсинга исключение ловилось outer try/except, и пропускался только этот пользователь, но expired_at/hwid_limit sync НЕ пропускался.
  - **Исправление:**
    - Убран `continue` при неудачном парсинге `onlineAt`. Теперь если `onlineAt` невалиден, пропускается только activation/connection блок, а expired_at/hwid_limit sync продолжается.
    - Вынесены `registration_changed`, `connection_changed`, `sanction_changed` флаги перед блоком `if online_at:`.
    - Блок батчевого обновления (bulk_update) и перепланирования задач вынесен после `if online_at:`, чтобы работать для всех вариантов.
    - Activation skip логирование переведено с `INFO` на `DEBUG` для уменьшения шума в логах.
  - **Добавлена диагностика:**
    - Новый endpoint `GET /remnawave/status` — возвращает статус последнего запуска `remnawave_updater` (время, summary, ошибки).
    - `_last_run_status` глобальный dict с метриками: `last_run_at`, `last_success_at`, `last_error`, `total_runs`, `total_errors`, `last_summary`.
    - `Checker summary` теперь логируется как структурированный dict с полями: `total_users_db`, `users_with_uuid`, `remnawave_users_fetched`, `onlineAt_available`, `activation_candidates`, `activation_notify_sent`, `duplicate_hwid_detected/blocked`, `not_found_in_remnawave`, `elapsed_seconds`, `errors`.
    - `send_admin_message()` теперь ведёт счётчик `_admin_msg_stats` (sent/failed/last_error), доступный через `/remnawave/status` → `admin_notifications`.
    - В warning при невалидном `onlineAt` добавлен тип значения для диагностики формата.
  - **Файлы:**
    - `bloobcat/routes/remnawave/catcher.py` — основной фикс + диагностика.
    - `bloobcat/bot/notifications/admin.py` — счётчик ошибок отправки.
  - **Тесты:** `pytest tests/test_remnawave_activation.py tests/test_hwid_antitwink.py tests/test_connections_process.py tests/test_resilience_hardening.py` → 49 passed.

- **fix(auth/referral-fk):** устранен крэш регистрации из-за `users.referred_by = 0` при FK-ограничении.
  - **Симптом в прод-логах:** `insert or update on table "users" violates foreign key constraint "users_referred_by_foreign"` с `Key (referred_by)=(0) is not present`.
  - Причина: в средах с self-FK на `users.referred_by -> users.id` значение `0` невалидно (должно быть `NULL`, если реферера нет).
  - `bloobcat/db/users.py`:
    - поле `referred_by` переведено в nullable (`null=True`, `default=None`);
    - в `Users.get_user(...).update_or_create(defaults=...)` для новых пользователей явно выставляется `referred_by=None`;
    - проверка возможности реф-привязки переведена с sentinel `0` на отсутствие значения (`not user.referred_by`).
  - `bloobcat/routes/auth.py`:
    - базовый `referred_by` в `/auth/telegram` теперь `None` (вместо `0`), чтобы не прокидывать невалидный sentinel.
  - Миграция: `migrations/models/83_20260220173000_fix_users_referred_by_nullable_fk.py`:
    - `DROP DEFAULT` для `users.referred_by`,
    - backfill `0 -> NULL`,
    - `DROP NOT NULL`.
  - Эффект: создание нового пользователя не падает на FK при отсутствии реферера.

- **fix(auth/registration-resilience):** снижена вероятность `Internal Server Error` на welcome-регистрации (`POST /auth/telegram`).
  - `bloobcat/db/users.py`:
    - `count_referrals()` переведен в non-blocking режим (ошибка логируется, но не валит регистрацию);
    - `schedule_user_tasks(user)` для нового пользователя обёрнут в `try/except` (ошибка планировщика не прерывает выдачу токена).
  - `bloobcat/routes/auth.py`:
    - основной путь `/auth/telegram` обёрнут защитным `try/except` с traceback-логированием;
    - если `Users.get_user(...)` неожиданно вернул `None`, endpoint больше не падает `500`, а возвращает `requires_registration=true`;
    - неожиданные ошибки теперь возвращают `503 Service temporarily unavailable` вместо необработанного `500`.
  - Цель: успешная регистрация пользователя даже при временной деградации side-effect логики (scheduler/referrals/внешние зависимости).

## 2026-02-20

- **fix(auth/registration-policy):** внедрен безопасный режим отложенной регистрации пользователя.
  - `bloobcat/routes/auth.py`:
    - добавлен `registerIntent` в `POST /auth/telegram`;
    - добавлен флаг ответа `requires_registration`;
    - без `registerIntent` и без `start_param` пользователь **не создается** (возвращается `requires_registration=true`);
    - при `start_param` (family/ref/qr) авто-регистрация сохранена.
  - `bloobcat/funcs/validate.py`:
    - отключено неявное создание пользователя на обычной валидации без `start_param`;
    - для отсутствующего пользователя теперь `403 User not registered`.
  - `bloobcat/bot/routes/start.py`:
    - убрано создание пользователя через `Users.get_user()` на `/start`;
    - админ-клавиатура ставится только для уже существующих админов (`get_or_none`).

- **fix(remnawave/activation-sync):** восстановлен сбор факта подключений и триггер активаций.
  - `bloobcat/routes/remnawave/catcher.py`:
    - добавлен helper `_extract_online_at()` с fallback: `onlineAt -> userTraffic.onlineAt -> userTraffic.firstConnectedAt`;
    - добавлен точечный `get_user_by_uuid` fallback для незарегистрированных пользователей без `connected_at`;
    - добавлено диагностическое `INFO`-логирование формата ответа RemnaWave (`has_userTraffic`, `sample_onlineAt`).
  - Эффект: снова заполняются `connected_at`/`connections`, срабатывает путь `is_registered` и `on_activated_key`.

- **fix(db/connections):** защита от дублей в `connections`.
  - `bloobcat/db/connections.py`:
    - добавлен `Meta.unique_together = (("user_id", "at"),)`;
    - `Connections.process()` теперь устойчив к race (`IntegrityError` -> безопасный `get`).
  - Новая миграция: `migrations/models/82_20260220150000_connections_unique_user_at.py`:
    - чистит исторические дубли;
    - добавляет DB-constraint `UNIQUE (user_id, at)`.

- **tests:** добавлены/обновлены регрессионные проверки.
  - Новый файл: `tests/test_auth_registration_modes.py` (режимы регистрации `/auth/telegram`).
  - Новый файл: `tests/test_connections_process.py` (обработка `IntegrityError` в `Connections.process`).
  - Обновлены:
    - `tests/test_resilience_hardening.py` (кейс `User not registered` + корректный tuple-return для `Users.get_user`);
    - `tests/test_remnawave_activation.py` (fallback `firstConnectedAt` через `_extract_online_at`).
  - Прогоны:
    - `py -3.12 -m pytest tests/test_auth_registration_modes.py tests/test_resilience_hardening.py tests/test_remnawave_activation.py tests/test_connections_process.py -q` -> `33 passed`;
    - `pnpm exec tsc --noEmit` (TelegramVPN) -> успешно.

## 2026-02-15

- **fix(remnawave/updater):** восстановлена наблюдаемость ошибок воркера активации и anti-bot.
  - `bloobcat/tasks/remnawave_updater.py`: удалено глотание ошибок в scheduler, добавлен `logger.exception(...)`.
  - `bloobcat/routes/remnawave/catcher.py`: расширено логирование traceback в критичных ветках updater; webhook на `/remnawave/webhook` переведен на фоновой запуск обновления с lock-защитой от параллельного выполнения.

- **fix(hwid/checker):** унифицирован парсинг устройств RemnaWave и усилена анти-твинк логика.
  - `bloobcat/routes/remnawave/hwid_utils.py`:
    - единый парсер `parse_remnawave_devices(...)` для форматов `list`, `response`, `response.devices`, `response.data`;
    - унифицированное извлечение HWID (`hwid | deviceId | id`);
    - helper `has_duplicate_hwid(...)` для проверки дубликатов между user UUID.
  - `bloobcat/routes/remnawave/catcher.py`: переход на общие HWID-утилиты + race-safe fallback при `IntegrityError` в `HwidDeviceLocal.get_or_create(...)`.
  - добавлен `bloobcat/routes/remnawave/activation_logic.py` с изолированным правилом `should_trigger_registration(...)`.

- **feat(in-app-notifications):** реализована backend-система динамических уведомлений.
  - Новые модели:
    - `InAppNotification` (title/body/start/end/limits/auto-hide/is_active);
    - `NotificationView` (user_id, notification_id, session_id, viewed_at).
  - Новые API:
    - `GET /notifications/active` — фильтр по периоду и лимитам per-user/per-session;
    - `POST /notifications/{id}/view` — фиксация показа (idempotent при duplicate).
  - Интеграция:
    - `bloobcat/routes/notifications.py`,
    - подключение в `bloobcat/routes/__init__.py`,
    - регистрация моделей в `bloobcat/clients.py`.
  - Миграции:
    - `79_20260219120000_in_app_notifications.py`,
    - `80_20260220120000_notification_views_unique.py` (UNIQUE `(user_id, notification_id, session_id)`).

- **feat(admin/notifications):** добавлен FastAdmin CRUD для `InAppNotification`.
  - Валидации:
    - `end_at >= start_at` (если обе даты заданы),
    - `max_per_user/max_per_session >= 1` или `null`,
    - `auto_hide_seconds >= 1` или `null`.

- **test(remnawave/hwid):** добавлены регресс-тесты:
  - `tests/test_remnawave_activation.py`,
  - `tests/test_hwid_antitwink.py`.

- Проверки:
  - `python -m py_compile` по измененным backend-файлам — успешно;
  - `ReadLints` — ошибок нет;
  - `pytest` локально блокируется из-за отсутствующей зависимости `yookassa` в окружении (ModuleNotFoundError при collection).

- **fix(admin/family-section-workspace):** добавлен fallback-workspace в секцию `Семья` карточки `users` Directus.
  - В `scripts/directus_super_setup.py` добавлена фаза `ensure_users_family_workspace_aliases`.
  - Создаются/обновляются два alias-поля, которые гарантированно видны между `Семья` и `Техника`:
    - `family_workspace_notice` (`presentation-notice`) с пояснением и fallback-логикой;
    - `family_workspace_links` (`presentation-links`) с быстрыми переходами в `family_members`, `family_invites`, `family_audit_logs` по текущему `{{id}}`.
  - Это дает рабочий интерфейс даже в инстансах, где relation list-o2m может не рендериться в item-form.
  - Применено на проде: `DIRECTUS_URL=https://admin.waiz-store.ru python scripts/directus_super_setup.py` — успешно.
  - Проверка: `python -m py_compile scripts/directus_super_setup.py` + `ReadLints` — OK.

- **fix(admin/family-self-heal):** добавлен защитный self-heal секции `Семья` в карточке `users` Directus.
  - `scripts/directus_super_setup.py`: добавлена фаза `ensure_users_family_section_ux`, которая повторно и идемпотентно:
    - восстанавливает `one_field` для family relations через `/relations/...`;
    - гарантирует наличие alias-полей `family_*` с `interface=list-o2m`;
    - форсирует `hidden=false`, `width=full`, корректный `sort`, `group=None`.
  - Добавлена верификация `verify_users_family_section_visibility` в финальные проверки setup.
  - Цель: исключить повторный регресс вида «есть divider `Семья`, но блоки внутри не рендерятся».
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно, `ReadLints` — без ошибок.

- **fix(db/family-fk-delete):** устранен `INTERNAL_SERVER_ERROR` при удалении пользователей из админки (`family_members_member_id_fkey`).
  - Добавлена миграция `migrations/models/77_20260215035500_fix_family_fk_on_delete.py`.
  - Все FK family-таблиц к `users` принудительно приведены к `ON DELETE CASCADE`:
    - `family_members.member_id`, `family_members.owner_id`
    - `family_invites.owner_id`
    - `family_devices.user_id`
    - `family_audit_logs.actor_id`, `family_audit_logs.owner_id`
  - Проверено на локальном Docker после `apply_migrations`:
    - до фикса часть FK была `NO ACTION`;
    - после фикса все перечисленные FK = `CASCADE`.

- **fix(admin/family-relations-runtime):** выявлена и устранена реальная причина пустой секции `Семья` в Directus.
  - Диагностика в локальном Docker показала:
    - `POST /items/directus_relations` возвращал `403 FORBIDDEN`;
    - relation rows для `family_members.owner_id/member_id` и `family_invites.owner_id` не получали `one_field`;
    - из-за этого блоки в карточке `users` не были связаны с данными.
  - Исправление в `scripts/directus_super_setup.py`:
    - `ensure_users_relations_ux.ensure_relation(...)` переведен с `/items/directus_relations` на официальный endpoint `/relations` (`GET/PATCH/POST /relations...`).
    - Для bootstrap-прав admin-policy расширен системный набор (`directus_collections` + fallback на `get_primary_policy_id_for_role(...)`).
  - Проверка после прогона setup:
    - `GET /relations/family_members/owner_id` -> `one_field=family_members_owner_list`;
    - `GET /relations/family_members/member_id` -> `one_field=family_members_member_list`;
    - `GET /relations/family_invites/owner_id` -> `one_field=family_invites_list`.
  - Локальный стенд: `docker compose up -d --build`, `python scripts/directus_super_setup.py` — успешно.

- **fix(admin/family-empty-section):** устранена причина пустого блока `Семья` для не-Administrator ролей в Directus.
  - `scripts/directus_super_setup.py`: добавлены family-коллекции в `apply_collection_ux` (видимы в навигации): `family_members`, `family_invites`, `family_devices`, `family_audit_logs`.
  - В `ensure_permissions_baseline` расширены права:
    - `manager_rw` и `admin_rw` теперь включают `family_members`, `family_invites`, `family_devices`, `family_audit_logs`;
    - `viewer_ro` включает read на те же family-коллекции.
  - Это убирает ситуацию, когда раздел в карточке пользователя отрисован, но relation-поля пустые/недоступные из-за отсутствия read/update прав на связанные коллекции.
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно.

- **fix(admin/family-visibility):** устранен риск "пустой" секции `Семья` в карточке `users` в Directus.
  - `scripts/directus_super_setup.py`: в `ensure_alias_o2m_field(...)` добавлен явный `meta.hidden = False` для alias o2m-полей (family/referred/relations), чтобы Directus не прятал их по умолчанию.
  - Порядок фаз в `main()` скорректирован: `ensure_users_relations_ux` теперь выполняется **до** `apply_users_form_ux`, чтобы form-UX/width/sort/visibility применялись уже к реально существующим alias-полям.
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно.

- **fix(admin/family):** расширена карточка пользователя в Directus для семейного контекста.
  - `scripts/directus_super_setup.py`: в `users` добавлены relation-блоки `family_members_owner_list`, `family_members_member_list`, `family_invites_list`.
  - В форму `users` добавлены размеры/сортировка для новых полей и отдельный divider `ui_divider_family` (секция "Семья").
  - В `ensure_users_relations_ux` добавлены o2m связи:
    - `family_members.owner_id -> users.family_members_owner_list`,
    - `family_members.member_id -> users.family_members_member_list`,
    - `family_invites.owner_id -> users.family_invites_list`.
  - В `apply_users_luxury_ux` добавлены шаблоны отображения (owner/member/status/allocated devices/usage).
  - Валидация: `python -m py_compile scripts/directus_super_setup.py` — успешно.

- **fix(family):** корректный расчет остатка устройств у главы семьи:
  - `bloobcat/routes/user.py`: `/user` для owner теперь возвращает `devices_limit` как остаток `база - активные выделения участникам`.
- **fix(family):** синхронизация реального лимита главы в RemnaWave при изменении состава/квоты семьи:
  - `bloobcat/routes/family_invites.py`: после `accept` (new/reactivate), `patch member`, `delete member`, `member leave` выполняется `_sync_owner_effective_remnawave_limit(...)`.
- **fix(family-legacy):** `bloobcat/routes/family.py` (`POST /subscription/family`) теперь учитывает аллокации участникам через `FamilyMembers` и ограничивает создание устройств по реальному остатку.
- Валидация: `python -m py_compile` по 3 файлам — успешно.
## 2026-02-15

- **fix(family):** реактивация участника по новому инвайту после статуса `disabled`:
  - файл: `bloobcat/routes/family_invites.py`, endpoint `POST /family/invites/{token}/accept`;
  - если member-запись уже существует и `status=disabled`/`allocated_devices=0`, теперь запись реактивируется (`status=active`, новый `allocated_devices`) и синхронизируется `hwid_limit` в RemnaWave.
- **fix(family):** чистка "мусора" в выдаче:
  - `GET /family/members` возвращает только активных участников (`status=active`, `allocated_devices>0`);
  - `GET /family/invites` возвращает только актуальные инвайты (не revoked, не expired, не exhausted).
- **fix(family):** family-контекст для участника считается только при активном членстве:
  - `bloobcat/routes/user.py` (`/user`) и `bloobcat/routes/family_invites.py` (`/family/membership`) фильтруют по `status=active` + `allocated_devices>0`.
- Проверка: `python -m py_compile bloobcat/routes/family_invites.py bloobcat/routes/user.py` — OK.
## Журнал изменений

### 2026-02-15 — subscription status: добавлен явный флаг автопродления

- В `bloobcat/routes/subscription.py` расширен ответ `GET /subscription/status`:
  - добавлено поле `autoRenewEnabled` (`bool(user.renew_id)`).
- `POST /subscription/cancel-renewal` теперь возвращает расширенный ответ:
  - `ok`,
  - `autoRenewEnabled: false`,
  - `wasAlreadyCancelled` (помогает фронту различать первое/повторное нажатие).
- Изменение обратно-совместимое для текущего фронта: дополнительные поля не ломают существующих клиентов.

### 2026-02-14 — найден root cause API 502: падение FastAPI на dependency-типе

- По логам с VPS (`bloobcat` + `caddy`) подтверждено:
  - `caddy` отдавал `502` из-за `connect: connection refused` к `bloobcat:33083`;
  - `bloobcat` падал на импорте роутов (`/status/{payment_id}`) с ошибкой FastAPI:
    - `Invalid args for response field ... starlette.requests.Request | None`.
- Корневая причина в `bloobcat/funcs/validate.py`:
  - dependency `validate()` имел сигнатуру `request: Request | None = None`, которая ломает FastAPI schema/dependency analysis на текущем стеке.
- Исправление:
  - сигнатура заменена на `request: Request = None`.
- Локальная проверка:
  - `python -m py_compile bloobcat/funcs/validate.py` — успешно.

### 2026-02-14 — пост-проверка после успешного деплоя: API все еще 502

- GitHub Actions run `22014681822` завершился `success` (step `Deploy to server` тоже `success`).
- Внешняя проверка прод-эндпоинтов показала:
  - `https://api.waiz-store.ru/health` -> `502 Bad Gateway` (Server: Caddy);
  - `https://api.waiz-store.ru/pay/tariffs` -> `502`;
  - `https://admin.waiz-store.ru/server/health` -> `200`.
- Вывод: деплой как процесс прошел, но backend app за reverse-proxy не отвечает корректно (или падает после старта); проблема не в GitHub workflow status.

### 2026-02-14 — RCA: `super_setup` падает `exit 137` без traceback

- Проверен свежий run `Auto Deploy Backend` (`22014607058`):
  - шаг доходит до `Directus super-setup attempt 1/4`;
  - затем immediate `Process completed with exit code 137`;
  - Python traceback отсутствует.
- Вывод: это не типичная логическая ошибка Python, а внешнее завершение процесса (OOM/SIGKILL/kill контейнера).
- Сделано 2 уровня hardening:
  - `.github/workflows/auto-deploy.yml`:
    - `super-setup` переведен в non-blocking post-deploy шаг (не валит релиз бэкенда),
    - при фейле печатаются диагностики `docker compose ps` + логи `bloobcat/directus`.
  - `scripts/directus_super_setup.py`:
    - уменьшена нагрузка на запрос пресетов (`/presets` limit `400` + ограниченный набор `fields`);
    - добавлены короткие паузы между тяжелыми фазами (`DIRECTUS_SUPER_SETUP_PHASE_PAUSE`, default `0.2s`);
    - фазы теперь логируются `Phase start: ...` для точного pinpoint места падения.

### 2026-02-14 — deploy hardening: non-blocking super-setup on exit 137

- По свежему failed run (`22014607058`) зафиксировано:
  - `Directus super-setup attempt 1/4` -> мгновенный `exit code 137` без Python traceback.
  - Это типично для принудительного убийства процесса (OOM/SIGKILL/перезапуск контейнера), а не для обычной логической ошибки в `directus_super_setup.py`.
- В `TVPN_BACK_END/.github/workflows/auto-deploy.yml` изменено поведение:
  - `super-setup` оставлен с retry, но больше не блокирует весь релиз при фейле;
  - при неуспехе выводятся диагностические логи `bloobcat` и `directus`, затем деплой продолжается.
- Цель: не срывать выпуск бэкенда из-за нестабильного post-deploy шага админского UX-setup.

### 2026-02-13 — hotfix workflow: корректная подстановка переменных в SSH heredoc

- Выявлена причина новых падений деплоя после предыдущего hardening-патча:
  - в `auto-deploy.yml` внутри `ssh << EOF` переменные shell (`$DIRECTUS_READY`, `$attempt`, `$max_attempts`, `$COMPOSE_ARGS`) интерпретировались некорректно;
  - в логе это проявлялось как:
    - `-bash: line ...: [: : integer expression expected`;
    - `sh: ...: [: Illegal number:`;
    - преждевременный fail `Directus super-setup failed after retries`.
- Исправление:
  - в проблемных местах переменные экранированы (`\$...`), чтобы они вычислялись на удаленном хосте в нужном контексте.

### 2026-02-13 — стабилизация auto-deploy Directus (готовность + retry)

- В `TVPN_BACK_END/.github/workflows/auto-deploy.yml` усилен этап ожидания Directus:
  - добавлен флаг `DIRECTUS_READY` и явная проверка после цикла ожидания;
  - если health не достигнут за таймаут — деплой завершается с диагностикой (`docker compose ps` + `logs --tail=120 directus`), вместо тихого продолжения.
- Устранен источник флаки-падений после рестарта Directus:
  - запуск `scripts/directus_super_setup.py` внутри `bloobcat` обернут в retry (`4` попытки, пауза `10s` между попытками).
- Контекст инцидента:
  - предыдущий падший run имел `Connection refused` к `directus:8055` на шаге super-setup и `exit code 137` при том, что `git pull --ff-only` прошёл успешно.

### 2026-02-13 — RCA деплоя: это не Git

- Проверен последний failed run `Auto Deploy Backend` (`21999897972`).
- На этапе `Deploy to server` Git-часть проходит штатно:
  - `git stash push -u ...` -> `No local changes to save`;
  - `git pull --ff-only` -> `Already up to date`.
- Фактическое падение происходит позже:
  - в `directus_super_setup.py` при логине в Directus (`http://directus:8055/auth/login`) получен `Connection refused`;
  - job завершается `Process completed with exit code 137`.
- Доп. наблюдение: в логе до SSH есть строка `fatal: not a git repository...`, но она не блокирует деплой и не является корневой причиной падения этого run.

### 2026-02-13 — уведомление о покупке семейной подписки + CTA в раздел семьи

- Добавлено отдельное пользовательское уведомление при успешной покупке семейной подписки (до 10 участников):
  - `bloobcat/bot/notifications/subscription/renewal.py`: новая функция `notify_family_purchase_success_yookassa()`;
  - текст уведомления уточняет, что теперь доступна семейная подписка до 10 человек и предлагает пригласить близких;
  - кнопка в уведомлении открывает фронтенд-раздел семьи: `/subscription/family`.
- Логика отправки интегрирована в покупку:
  - `bloobcat/routes/payment.py`: добавлен helper `_notify_successful_purchase()` с выбором family-уведомления при `device_count >= 10`;
  - применено в трех сценариях: webhook успеха YooKassa, fallback-активация оплаты, покупка с бонусного баланса.

### 2026-02-13 — Логи ошибок в админке + triage workflow

- Добавлен workflow-статус в backend-модель логов ошибок:
  - `bloobcat/db/error_reports.py`: поля `triage_status`, `triage_owner`, `triage_note`, `triage_updated_at`.
  - `bloobcat/routes/error_reports.py`: новые входящие баг-репорты создаются со статусом `triage_status='new'`.
  - Миграция: `migrations/models/75_20260213_error_reports_triage_workflow.py`.
- Расширен setup Directus:
  - `scripts/directus_super_setup.py`:
    - коллекция `error_reports` вынесена в левое меню: группа `Служебное` → `Логи ошибок`;
    - добавлены RU-notes/переводы для полей error reports;
    - `triage_status` настроен как dropdown (`new / in_progress / resolved`);
    - добавлен UX формы/списка для triage;
    - права: `Manager` получил `read + update` для `error_reports` (без create/delete), `Viewer` — `read`, `Administrator` — полный доступ;
    - добавлены bookmarks: «не взяты», «в работе», «исправлены».
- В модуле `tvpn-home` добавлен быстрый переход в раздел:
  - `directus/extensions/tvpn-home/src/module.vue` → пункт навигации `Логи ошибок` (`/content/error_reports`).

### 2026-02-13 — SLA по баг-репортам (критичность + просрочка 24ч)

- Расширена модель `error_reports`:
  - `triage_severity` (`low/medium/high/critical`, default `medium`);
  - `triage_due_at` (SLA дедлайн triage).
- Добавлена миграция:
  - `migrations/models/76_20260213_error_reports_sla_and_severity.py`
  - для исторических записей `triage_due_at` backfill: `created_at + 24h`.
- Обновлен приём баг-репортов:
  - `bloobcat/routes/error_reports.py` задает новым логам `triage_due_at = now + 24h`.
- В Directus setup:
  - поле `triage_severity` настроено как dropdown;
  - добавлен bookmark **«Баг-репорты: просрочен triage (24ч)»** (`status=new` и `triage_due_at <= $NOW`);
  - в bookmarks/таблицах добавлены колонки критичности и SLA дедлайна.

### 2026-02-12 — моментальный пересчет тарифов при сохранении в Directus (Hook)

- Добавлен защищенный endpoint вычисления цены:
  - `POST /admin/integration/tariffs/compute-pricing`
  - файл: `bloobcat/routes/admin_integration.py`
  - логика: `bloobcat/services/admin_integration.py::compute_tariff_effective_pricing`.
- Расширен Directus hook `directus/extensions/remnawave-sync`:
  - `filter("items.create"/"items.update")` для коллекции `tariffs`;
  - перед сохранением вызывается backend compute endpoint и в payload подставляются `base_price` и `progressive_multiplier`;
  - за счет filter-хука пересчет происходит в момент сохранения карточки (без ожидания фронтовых/API обращений).
- Безопасность:
  - hook использует уже существующий `ADMIN_INTEGRATION_TOKEN`;
  - при любой ошибке вычисления сохранение не блокируется (fallback: payload без пересчета).
- UX/надежность setup:
  - в `scripts/directus_super_setup.py` добавлена проверка `verify_tariffs_form_visibility()` для контроля, что ключевые поля `tariffs` не скрыты.

### 2026-02-12 — UX-апгрейд админки тарифов: финальные цены карточек + понятная family-логика

- Цель:
  - упростить управление тарифами в Directus: редактировать именно финальную цену карточки, а не вручную подбирать `base_price`/`progressive_multiplier`;
  - сделать структуру формы тарифов более понятной и ближе к фактическим карточкам витрины.
- Что изменено:
  - `bloobcat/db/tariff.py`:
    - добавлены поля `family_plan_enabled`, `final_price_default`, `final_price_family`;
    - добавлен авто-пересчет эффективных `base_price/progressive_multiplier` из финальных цен карточек;
    - `calculate_price()` и snapshot-цены теперь используют эффективные (пересчитанные) параметры.
  - `migrations/models/74_20260212_add_tariff_card_pricing_fields.py`:
    - миграция новых полей карточек тарифа.
  - `bloobcat/routes/subscription.py`:
    - семейная карточка 12 месяцев учитывает `family_plan_enabled`;
    - покупка по `12months_family` доступна только если семейный режим включен.
  - `bloobcat/routes/payment.py`:
    - в snapshot `ActiveTariffs.progressive_multiplier` сохраняется эффективный множитель (после авто-пересчета из финальных цен).
  - `scripts/directus_super_setup.py`:
    - обновлены заметки/переводы для `tariffs` с акцентом на редактирование финальных цен;
    - добавлены `apply_tariffs_form_ux()` и `ensure_tariffs_presentation_dividers()`:
      - секции формы: карточка, финальные цены, лимиты/family, формула, LTE;
      - заметки-пояснения по family/non-family;
    - добавлены presets: `Тарифы: карточки витрины`, `Тарифы: 12 месяцев + family`.
  - `scripts/vps/seed_tariffs.sql`:
    - сид дополнен новыми полями и финальными ценами карточек.

### 2026-02-12 — синхронизация тарифов frontend ↔ admin (Directus) ↔ backend

- Проблема:
  - фронт строил планы из `GET /pay/tariffs` + локального маппинга, а не из `GET /subscription/plans`;
  - в `tariffs` не было `is_active` и явных лимитов устройств для витрины планов;
  - в админке нельзя было корректно управлять доступностью тарифа и лимитами планов.
- Что изменено:
  - `bloobcat/db/tariff.py`:
    - добавлены поля `is_active`, `devices_limit_default`, `devices_limit_family`;
    - `calculate_price()` переведен на `round`, чтобы цены совпадали с фронтовой витриной (290/748/1287/2189/4489).
  - `bloobcat/routes/payment.py`:
    - `GET /pay/tariffs` теперь возвращает только активные тарифы (`is_active=true`);
    - `GET /pay/{tariff_id}` блокирует покупку неактивного тарифа;
    - fallback-обработка платежа стала устойчивой к удаленному/неактивному тарифу (создается snapshot `ActiveTariff`, чтобы не терять состояние подписки).
  - `bloobcat/routes/subscription.py`:
    - `GET /subscription/plans` строится только из активных тарифов;
    - добавлен `tariffId` в ответ планов;
    - лимиты устройств для планов берутся из `tariffs.devices_limit_default/family`;
    - семейный план остается 12-месячным вариантом (не отдельной базовой записью тарифа).
  - `migrations/models/73_20260212_add_tariffs_activation_and_limits.py`:
    - миграция полей `is_active`, `devices_limit_default`, `devices_limit_family`.
  - `scripts/vps/seed_tariffs.sql`:
    - сид обновлен под новые поля и синхронные лимиты устройств.
  - `scripts/directus_super_setup.py`:
    - добавлены русские подсказки/лейблы по новым полям тарифов в админке.

### 2026-02-12 — расследование: 4 одинаковых trial-сообщения после регистрации

- Симптом:
  - новый пользователь получил 4 одинаковых сообщения «Поздравляем ... бесплатный 3-дневный доступ ...» сразу после первого входа.
- Подтвержденный источник текста:
  - `bloobcat/bot/notifications/trial/granted.py` (`notify_trial_granted`).
- Где вызывается отправка:
  - `bloobcat/db/users.py` в `_ensure_remnawave_user()`, когда одновременно выполняются условия:
    - `expired_at is None`,
    - `used_trial = False`,
    - и идет первичная привязка к RemnaWave.
- Почему возможны дубли:
  - на старте фронт может параллельно дергать несколько endpoint (`/auth/telegram`, `/user`, `/devices` и др. через `validate -> Users.get_user`);
  - локальный `asyncio.Lock` в `_ensure_remnawave_user` защищает только в рамках одного процесса;
  - при нескольких воркерах/инстансах нет межпроцессной дедупликации именно для `trial_granted` (нет `NotificationMarks` на этот тип уведомления).
- Важное уточнение по "регистрации":
  - пользователь создается в БД при первом `Users.get_user()` (обычно на `/auth/telegram`/`validate`);
  - флаг `is_registered=True` выставляется позже в `bloobcat/routes/remnawave/catcher.py` (`remnawave_updater`) только после первого фактического подключения к VPN (`onlineAt` в RemnaWave).

### 2026-02-12 — Directus users: пустые секции + клики из списка

- Симптомы:
  - в карточке `users` видны только заголовки секций (`Основное`, `Подписка` и т.д.), а поля не отображаются;
  - из списка пользователей переход в карточку может работать нестабильно.
- Подтвержденная причина:
  - в старых конфигурациях часть полей `users` могла остаться с `meta.hidden=true`;
  - setup ранее не принудительно снимал скрытие при раскладке полей по секциям;
  - часть пресетов могла иметь некорректный `layout_query` (невалидный `fields`), что влияет на открытие item-view.
- Что исправлено в `scripts/directus_super_setup.py`:
  - `apply_users_form_ux()` теперь выставляет `hidden=False` для всех ключевых полей карточки;
  - усилено восстановление пресетов:
    - `ensure_tabular_fields_include_pk()` лечит битые `layout_query/tabular/fields`;
    - `ensure_cards_fields_include_pk()` лечит битые `layout_query/cards/fields`.
- Проверка:
  - `python -m py_compile scripts/directus_super_setup.py` — успешно.
  - setup применен на локальный инстанс: `python scripts/directus_super_setup.py` — успешно.
  - setup применен на прод: `DIRECTUS_URL=https://admin.waiz-store.ru python scripts/directus_super_setup.py` — успешно.
  - API-проверка после применения:
    - у полей `users` (`id`, `username`, `full_name`, `email`, `expired_at`, `balance`, `is_blocked`, `referred_by`) `meta.hidden=false`;
    - у пользовательского tabular-пресета `users` присутствует поле `id` (критично для клика/перехода в item-view).

### 2026-02-12 — Премиум-доводка карточки users (прод)

- Что улучшено в `scripts/directus_super_setup.py`:
  - `is_admin` перенесен в секцию `Основное`:
    - `group=ui_divider_overview`, `sort=9`, `interface=toggle`, `hidden=false`;
  - устранен конфликт сортировки в секции `Операции`:
    - `ui_divider_ops` оставлен на `sort=66`, поля смещены начиная с `67`;
  - стабилизировано создание o2m-блоков (логи/связи):
    - alias-поле создается/патчится до обновления relation metadata;
    - из payload убран `special` для `/fields/*` (совместимость с Directus API, где это поле запрещено);
    - добавлен безопасный разбор `400 already exists`.
- Результат на проде (`admin.waiz-store.ru`):
  - setup повторно применен успешно;
  - карточка `users` получила корректную структуру с блоком `Операции`;
  - alias-поля операций доступны по endpoint `GET /fields/users/<alias>`:
    - `referred_users_list`, `active_tariffs_list`, `promo_usages_list`,
      `notification_marks_list`, `family_devices_list`,
      `partner_withdrawals_list`, `partner_earnings_list`, `family_audit_logs_owner`.

### 2026-02-12 — финальный фикс "пустых секций" users

- Симптом:
  - в карточке отображались только divider-блоки и `is_admin`, остальные поля не рендерились.
- Причина:
  - для части полей `users` в `meta.interface` было `null`; в текущем Directus UI такие поля могли не показываться в item-view.
  - отдельный прогон setup на проде мог падать из-за transient `503` на PATCH `/fields/...`.
- Исправлено:
  - в `ensure_users_presentation_dividers()` добавлен набор явных интерфейсов:
    - `input` для текстовых/числовых полей,
    - `toggle` для булевых,
    - существующие `datetime`, `id-link-editor`, `list-o2m` сохранены.
  - в `patch_field_meta()` добавлен retry на `502/503/504` (3 попытки с backoff), чтобы setup не обрывался посередине.
- Проверка:
  - повторный прод-прогон завершился успешно;
  - подтверждено по API, что ключевые поля (`username`, `full_name`, `email`, `balance`, `lte_gb_total`, `referrals`, `remnawave_uuid` и др.) имеют `interface` и `hidden=false`.

### 2026-02-12 — root cause найден: `group` на divider скрывал поля

- Факт:
  - на проде поля начинали отображаться сразу после снятия `meta.group` у `users`-полей;
  - при `group=ui_divider_*` (где divider = `presentation-divider`) UI рендерил только разделители/пустой контент.
- Исправление:
  - `users`-поля переведены в режим **без `group`** (`group=None`);
  - разделы сохраняются через `presentation-divider` и `sort`, но без привязки полей к divider как к group-якорю;
  - сохранены явные `interface` для полей (`input/toggle/datetime/id-link-editor/list-o2m`);
  - дополнительно выданы явные read-права Manager/Viewer на schema-коллекции:
    - `directus_collections`, `directus_fields`, `directus_relations`, `directus_presets`.
- Результат:
  - карточка `users` и форма создания элемента снова рендерят все ключевые поля;
  - редактирование пользователя доступно.

### 2026-02-12 — luxury UX слой users + русификация англоязычных пунктов

- Что добавлено в `scripts/directus_super_setup.py`:
  - новый этап `apply_users_luxury_ux()`:
    - KPI-блок вверху карточки (`ui_divider_kpi`): ключевые поля вынесены в top-zone (`balance`, `expired_at`, `lte_gb_total`, `is_subscribed`, `is_blocked`, `referrals`);
    - блок быстрых действий (`ui_divider_quick_actions`) с 1-клик переходами:
      - `referred_by` (к родителю-рефереру),
      - `active_tariff_id` (к активному тарифу),
      - быстрые тех-поля `familyurl`, `remnawave_uuid`;
    - улучшены шаблоны list-o2m для логов/истории:
      - `active_tariffs_list`, `promo_usages_list`, `notification_marks_list`, `family_audit_logs_owner`.
  - расширена русификация и пояснения в `apply_field_notes_ru()`:
    - добавлены `translations` и `note` для англоязычных полей `users` (`is_admin`, `is_subscribed`, `used_trial`, `active_tariff_id`, `remnawave_uuid` и др.);
    - в UI карточки лейблы и описания стали русскими и операционно понятными.
  - дополнительно усилены пояснения для системно-английских полей:
    - `username`, `full_name`, `created_at`, `prize_wheel_attempts`.
- Важно:
  - UX-слой реализован **без `meta.group`**, чтобы не вернуть баг с пустыми секциями на проде.
- Применение:
  - синтаксис: `python -m py_compile scripts/directus_super_setup.py` — успешно;
  - прод: `DIRECTUS_URL=https://admin.waiz-store.ru python scripts/directus_super_setup.py` — успешно.

### 2026-02-12 — корректировка luxury-слоя без вмешательства в прошлую раскладку

- Обратная связь:
  - после первой версии luxury-слоя поля визуально "перемешались".
- Причина:
  - в `apply_users_luxury_ux()` были патчи `sort/width` для существующих полей (`balance`, `expired_at`, `referred_by`, `active_tariff_id` и др.), что переупорядочивало базовую раскладку.
- Исправление:
  - из luxury-слоя удалены все мутации `sort/width` существующих полей;
  - прошлый порядок карточки оставлен как в стабильной итерации;
  - luxury-надстройка переведена в безопасный режим: только enrichment (notes/templates) + опциональные divider-поля в конце формы (`sort=9900+`), чтобы не влиять на основной UX.
- Дополнительно:
  - русские названия/пояснения для ранее англоязычных полей сохранены (`Логин`, `ФИО`, `Создан`, `Попытки колеса` и др.).
- Применение/проверка:
  - `python -m py_compile scripts/directus_super_setup.py` — успешно;
  - прод-прогон setup — успешно;
  - карточка `users` открывается, поля отображаются в прежнем порядке (без повторного перемешивания).

### 2026-02-12 — заполнение пустых luxury-секций KPI/быстрые действия

- Обратная связь:
  - в карточке `users` блоки `KPI и быстрый статус` и `Быстрые действия` отображались пустыми.
- Причина:
  - после безопасного переноса этих divider-полей в самый низ (`sort=9900+`) под ними не было отдельных полей/виджетов.
- Что добавлено:
  - в `apply_users_luxury_ux()` добавлены alias-поля (без изменения существующего порядка полей):
    - `ui_kpi_notice` (`presentation-notice`) — назначение KPI-блока;
    - `ui_kpi_links` (`presentation-links`) — быстрые переходы к связанным спискам:
      - `active_tariffs` по `user_id={{id}}`,
      - `promo_usages` по `user_id={{id}}`,
      - `notification_marks` по `user_id={{id}}`;
    - `ui_quick_actions_notice` (`presentation-notice`) — объяснение quick actions;
    - `ui_quick_actions_links` (`presentation-links`) — 1-клик переходы:
      - к рефереру `users/{{referred_by}}`,
      - к активному тарифу `active_tariffs/{{active_tariff_id}}`,
      - к списку рефералов `users?filter[referred_by][_eq]={{id}}`.
- Доп. фикс:
  - в URL button-links заменен префикс `/admin/content/...` на `/content/...`, т.к. при старом варианте роутер Directus строил путь как `/admin/admin/...` и открывал 404.
- Важно:
  - существующие поля и их сортировка не менялись;
  - добавлены только новые alias/presentation-блоки под уже существующие divider-секции.

### 2026-02-12 — объединение KPI/действий в один раздел + фильтры по текущему пользователю

- Запрос:
  - объединить `KPI и быстрый статус` и `Быстрые действия` в единый блок;
  - гарантировать, что переходы по логам/историям показывают данные только по текущему пользователю.
- Что изменено в `apply_users_luxury_ux()`:
  - единый раздел:
    - `ui_divider_kpi` переименован в `KPI и быстрые действия`;
    - `ui_divider_quick_actions` скрыт (`hidden=true`) как устаревший второй заголовок;
    - блоки `ui_quick_actions_notice` и `ui_quick_actions_links` подняты в тот же раздел (`sort=9903/9904`).
  - фильтры/переходы:
    - `active_tariffs` — `/content/active_tariffs?filter[user_id][_eq]={{id}}`;
    - `promo_usages` — `/content/promo_usages?filter[user_id][_eq]={{id}}`;
    - `notification_marks` — `/content/notification_marks?filter[user_id][_eq]={{id}}`;
    - добавлен `family_audit_logs` — `/content/family_audit_logs?filter[owner_id][_eq]={{id}}`;
    - `referred_by`/`active_tariff_id` остаются точечными переходами к связанным item;
    - список рефералов — `/content/users?filter[referred_by][_eq]={{id}}`.
- Результат UX:
  - один компактный операционный раздел внизу карточки;
  - все список-переходы открываются уже отфильтрованными по текущему пользователю.

### 2026-02-12 — добавлен индикатор filtered-view в карточке users

- Запрос:
  - показать оператору явный маленький индикатор, что переходы работают в режиме фильтра по текущему пользователю.
- Реализация:
  - в `apply_users_luxury_ux()` добавлено alias-поле `ui_filter_indicator`:
    - `interface: presentation-notice`,
    - `icon: filter_alt`,
    - текст: «Фильтр активен: переходы ниже открываются только в filtered-view по текущему пользователю.»,
    - компактное размещение `width=half`.
  - скорректированы `sort` у соседних блоков (`ui_kpi_links`, `ui_quick_actions_notice`, `ui_quick_actions_links`), чтобы индикатор стоял рядом/перед быстрыми ссылками.

### 2026-02-12 — фикс deep-link фильтров: формат `filter=<json>`

- Симптом:
  - по кнопкам открывался список, но фильтрация в Data Studio могла не применяться.
- Причина:
  - формат query `filter[field][_eq]=...` в deep-links для интерфейса `presentation-links` на текущем инстансе/версии мог не интерпретироваться как активный state-фильтр списка.
- Исправление:
  - все ссылки переведены на формат `filter=<json>` (url-encoded), например:
    - `/content/active_tariffs?filter=%7B%22user_id%22%3A%7B%22_eq%22%3A%22{{id}}%22%7D%7D`
    - `/content/users?filter=%7B%22referred_by%22%3A%7B%22_eq%22%3A%22{{id}}%22%7D%7D`
- Ожидаемый результат:
  - списки открываются сразу с примененным фильтром по текущему пользователю.

### 2026-02-12 — fallback на гарантированно фильтрованные истории в карточке users

- Проблема:
  - в текущем инстансе Data Studio `content`-роутер не отражал/не применял query `filter` как ожидаемый active-filter state списка (даже при валидном формате).
- Решение:
  - быстрые ссылки переведены с внешних collection-списков на встроенные relation-истории внутри карточки пользователя (через hash-якоря):
    - `#active_tariffs_list`,
    - `#promo_usages_list`,
    - `#notification_marks_list`,
    - `#family_audit_logs_owner`,
    - `#referred_users_list`.
- Почему это корректно:
  - эти списки — `o2m` связи текущего пользователя, поэтому данные по определению уже фильтрованы только по нему.
- Доп. правка UX:
  - текст индикатора обновлен, чтобы явно отражать новую механику: переход к встроенным спискам карточки пользователя (без заявлений про collection filtered-view).

### 2026-02-12 — расследование дублей подписки в RemnaWave (один user -> много remote users)

- Симптом:
  - у одного Telegram user в RemnaWave появляется несколько пользователей/подписок (`id`, `id_1`, `id_2`...).
- Подтвержденные причины в коде:
  - на фронте при старте параллельно уходят `POST /auth/telegram`, `GET /user`, `GET /devices` (до появления Bearer токена часто с `initData`);
  - при `initData` backend в `validate()` вызывает `Users.get_user()`, а он вызывает `_ensure_remnawave_user()` если `remnawave_uuid` пустой;
  - гонка: несколько одновременных запросов видят `remnawave_uuid=None` и одновременно доходят до `create_user`;
  - `_ensure_remnawave_user()` на коллизии имени (`already exists`) перебирает `username` с суффиксами `_1/_2`, фактически создавая дубликаты в RemnaWave;
  - `recreate_remnawave_user()` используется в `remnawave_updater` и `payment` при `User not found` и не удаляет старого remote user перед пересозданием.
- Дополнительный риск:
  - в `remnawave/catcher.py` флаг `update_in_progress` не защищен lock'ом (check/set не атомарны), что может запускать конкурирующие апдейтеры.
- Где подтверждено:
  - `TVPN_BACK_END/bloobcat/db/users.py` (`_ensure_remnawave_user`, `recreate_remnawave_user`, `get_user`);
  - `TVPN_BACK_END/bloobcat/routes/remnawave/catcher.py` (`update_in_progress`, `recreate_remnawave_user` вызовы);
  - `TVPN_BACK_END/bloobcat/routes/payment.py` (retry/update + `recreate_remnawave_user`);
  - `TelegramVPN/src/hooks/useAuthBootstrap.ts`, `useTvpnUserSync.ts`, `useTvpnDevicesSync.ts` (параллельный старт запросов).
- Рекомендованный план фикса:
  - сделать идемпотентность создания RemnaWave user на backend (пер-пользовательный lock + повторная проверка `remnawave_uuid` после lock);
  - убрать автогенерацию `username_*` как fallback для одного Telegram user, сначала искать существующего remote user по `telegramId/email` (или иной стабильной связке);
  - сделать `recreate_remnawave_user()` безопасным: сначала попытка rebind к существующему remote user, затем controlled recreate (по политике), опционально удаление/деактивация старого;
  - защитить `remnawave_updater` через `asyncio.Lock`;
  - добавить диагностику: `user_id`, caller, old/new uuid, причина recreate, correlation_id.

### 2026-02-12 — фикс дублей RemnaWave без ломки синхронизации

- Реализовано в `TVPN_BACK_END`:
  - `bloobcat/db/users.py`:
    - добавлен пер-пользовательный `asyncio.Lock` для `_ensure_remnawave_user()` и `recreate_remnawave_user()`;
    - удалена стратегия создания `username` с суффиксами `_1/_2` (источник дублей);
    - при коллизии имени добавлен **rebind** к существующему remote user по lookup (`telegramId/email/username`) + fallback-поиск по `/api/users`;
    - в `recreate_remnawave_user()` добавлена проверка: если текущий UUID уже валиден, пересоздание не выполняется.
  - `bloobcat/routes/remnawave/client.py`:
    - добавлены методы lookup: `get_user_by_telegram_id`, `get_user_by_email`, `get_user_by_username` (без retry, чтобы не ждать 60с на старых панелях без endpoint'ов).
  - `bloobcat/routes/remnawave/catcher.py`:
    - `update_in_progress` заменен на `asyncio.Lock` + non-blocking acquire через `wait_for(..., timeout=0)` для безопасного skip при параллельных запусках.
- Проверка:
  - `python -m py_compile bloobcat/db/users.py bloobcat/routes/remnawave/client.py bloobcat/routes/remnawave/catcher.py` — успешно.
- Известный компромисс:
  - в админских миграционных отчётах (`miglte/migexternal`) счётчик `recreated` может включать случаи rebind (это не ломает синхронизацию, но меняет точность формулировки «пересоздано»).

### 2026-02-12 — hardening под высокий трафик / штормы / ddos-like пики

- Дополнительно усилено:
  - `bloobcat/db/users.py`:
    - добавлен межпроцессный lock через PostgreSQL advisory lock (`pg_try_advisory_lock`) поверх in-process lock, чтобы защитить создание/пересоздание пользователя и при нескольких воркерах/инстансах;
    - fallback-safe поведение: при недоступности advisory lock система не падает, продолжает работу с локальным lock и логирует предупреждение.
  - `bloobcat/funcs/validate.py`:
    - добавлен fast-path: если пользователь уже есть и имеет `remnawave_uuid`, возвращаем его без повторного `Users.get_user()` (снижение нагрузки на БД и side-effects при initData-шторме).
  - `bloobcat/middleware/rate_limit.py`:
    - rate limiter сделан thread-safe/async-safe (`asyncio.Lock`);
    - добавлены IP-лимиты для горячих endpoint'ов: `/auth/telegram`, `/user`, `/devices`, `/app/info`, `/partner/summary`;
    - сохранены и адаптированы существующие user-level лимиты для операций (`reset_devices`, `family/revoke`, `promo/*`).
- Верификация:
  - `python -m py_compile bloobcat/db/users.py bloobcat/funcs/validate.py bloobcat/middleware/rate_limit.py` — успешно.
  - lint по измененным файлам — без ошибок.

### 2026-02-12 — post-review приоритетные фиксы (устранение найденных багов)

- По результатам code review внесены корректировки:
  - `bloobcat/middleware/rate_limit.py`:
    - исправлен bypass user-rate-limit для Bearer токенов (`get_user_id_from_request` теперь понимает JWT и initData);
    - добавлена защита от memory leak в limiter (очистка пустых ключей после window cleanup);
    - ужесточена работа с `X-Forwarded-For`: используется только если прямой клиент в `RATE_LIMIT_TRUSTED_PROXIES`;
    - добавлен fallback IP limiter для чувствительных endpoint'ов при `user_id=None`.
  - `bloobcat/funcs/validate.py`:
    - fast-path ограничен условием `not start_param`, чтобы не потерять referral/utm обработку.
  - `bloobcat/db/users.py`:
    - удален advisory-lock слой (как потенциально нестабильный из-за session-scoped семантики и пула соединений);
    - сохранен устойчивый in-process lock + идемпотентный rebind/create подход;
    - при rebind в `_ensure_remnawave_user` сохраняются также trial-поля (`is_trial`, `used_trial`, `expired_at`) во избежание повторной выдачи триала.
- Проверка:
  - `python -m py_compile ...` — успешно;
  - lint по измененным файлам — без ошибок.

### 2026-02-12 — фактическая верификация отказоустойчивости (контейнерный smoke + stress)

- Выполнено:
  - контейнерная проверка регрессионных тестов: `tests/test_resilience_hardening.py` (внутри `bloobcat` контейнера);
  - burst-тесты по HTTP на локально поднятом `bloobcat`:
    - `GET /app/info` (360 запросов, параллелизм 80),
    - `POST /promo/redeem` без авторизации (90 запросов, параллелизм 50).
- Результаты:
  - Unit/regression: `7 passed` (warnings только deprecation от tortoise);
  - `/app/info`: `200: 300`, `429: 60` — лимит отрабатывает корректно;
  - `/promo/redeem` unauth: `403: 60`, `429: 30` — fallback limiter для неавторизованных штормов срабатывает.
- Важный найденный и устраненный дефект:
  - при ранней версии middleware возвращался `500` вместо `429` (из-за `raise HTTPException` в middleware);
  - исправлено на явный `JSONResponse(status_code=429, Retry-After=...)`.
- Итог:
  - после исправлений текущая итерация подтверждена как рабочая в контейнерной среде с нагрузочными burst-сценариями.

### 2026-02-13 — Directus `tvpn-home`: кнопка сохранения техработ

- Контекст:
  - После вынесения настроек техработ в отдельную карточку кнопка "Сохранить" оставалась неактивной.
  - Причина: кнопка была заблокирована условием `:disabled="!settingsId"`, а `settingsId` может быть пустым, если запись в `tvpn_admin_settings` еще не создана.
- Исправление в `directus/extensions/tvpn-home/src/module.vue`:
  - Для обеих кнопок сохранения изменено условие блокировки на `:disabled="settingsSaving"`.
  - В `saveSettings()` добавлен upsert-подобный сценарий:
    - если `settingsId` отсутствует -> `POST /items/tvpn_admin_settings` и сохранить `id`;
    - иначе -> `PATCH /items/tvpn_admin_settings/{id}`.
- Верификация:
  - lint по файлу `module.vue` — без ошибок;
  - пересборка расширения `tvpn-home` (`npm run build`) — успешно.

### 2026-02-13 — Directus `tvpn-home`: сохранение через singleton endpoint

- Проблема:
  - В UI появлялась ошибка "Не удалось сохранить настройки (нет прав или коллекция не создана)".
  - Основная причина: роль часто имеет `update`, но не имеет `create`; сценарий с `POST` при пустом `settingsId` ломался.
- Исправление в `directus/extensions/tvpn-home/src/module.vue`:
  - `loadSettings()` сначала читает `GET /items/tvpn_admin_settings/singleton`, затем fallback на list endpoint.
  - `saveSettings()` сначала пишет `PATCH /items/tvpn_admin_settings/singleton`.
  - При `403/404` добавлен fallback на старый сценарий (`POST`/`PATCH by id`), чтобы покрыть нестандартные окружения.
  - Добавлены более точные тексты ошибок по статусам `401/403/404`.
- Верификация:
  - lint по `module.vue` — без ошибок.
  - сборка `tvpn-home` (`npm run build`) — успешно.
  - `python -m py_compile scripts/directus_super_setup.py` — успешно.

- Дополнительно:
  - В `scripts/directus_super_setup.py` для коллекции `tvpn_admin_settings` добавлена выдача `create` для роли Manager (вместе с `read/update`) для надежного fallback-создания записи.

### 2026-02-13 — RCA по ошибке "Коллекция tvpn_admin_settings не найдена"

- Что проверено вручную:
  - `directus_super_setup.py` не выполнялся из-за недоступного `http://localhost:8055` (контейнер Directus не был запущен).
  - После `docker compose up -d directus` и повторного запуска setup скрипт завершился успешно.
  - В БД подтверждено наличие коллекции: `directus_collections.collection='tvpn_admin_settings'`, `singleton=true`.
- Найден корневой дефект во frontend-модуле `tvpn-home`:
  - fallback сохранения использовал `POST /items/tvpn_admin_settings`, а для singleton в текущем Directus этот route возвращает `404 ROUTE_NOT_FOUND`.
  - из-за этого UI показывал сообщение "коллекция не найдена", хотя коллекция существовала.
- Исправление в `directus/extensions/tvpn-home/src/module.vue`:
  - чтение настроек переведено на `GET /items/tvpn_admin_settings` с корректным разбором singleton-ответа-объекта;
  - сохранение переведено на `PATCH /items/tvpn_admin_settings` (рабочий singleton endpoint для данного окружения);
  - оставлен fallback `PATCH /items/tvpn_admin_settings/{id}` для legacy/non-singleton сценариев.
- Верификация:
  - `npm run build` для `tvpn-home` — успешно;
  - API check: `GET /items/tvpn_admin_settings` возвращает `200` и `{"data":{"id":1}}`.

### 2026-02-13 — финальный фикс сохранения техработ (права/поля)

- Симптом:
  - В админке при сохранении блока "Технические работы" сохранялся UI-error `Не удалось сохранить настройки...`.
- Корневая причина:
  - В таблице `tvpn_admin_settings` в БД был только столбец `id` (остальные поля отсутствовали).
  - Из-за этого `PATCH /items/tvpn_admin_settings` падал на payload с `maintenance_*` и алерт-полями.
  - Почему так случилось: в `scripts/directus_super_setup.py` функция `ensure_field()` прекращала работу на `GET /fields/...` со статусом `403` и не пробовала создать поле, хотя `POST /fields/...` в этой инсталляции разрешен.
- Исправление:
  - `scripts/directus_super_setup.py` (`ensure_field`) изменен: при любом `GET != 200` выполняется попытка `POST /fields/{collection}` (с сохранением idempotency через `409`/`401`/`403`).
  - Скрипт `directus_super_setup.py` повторно выполнен.
- Подтверждение:
  - В `tvpn_admin_settings` теперь есть все колонки (`maintenance_mode`, `maintenance_message`, `alerts_enabled`, пороговые поля и т.д.).
  - `PATCH /items/tvpn_admin_settings` возвращает `200` и корректно обновляет значения.

### 2026-02-13 — hardening сохранения техработ (multi-endpoint fallback + post-create grants)

- Симптом:
  - Пользователь продолжал видеть ошибку сохранения в блоке "Технические работы" даже после базовых фиксов.
- Что доработано:
  - `directus/extensions/tvpn-home/src/module.vue`:
    - `loadSettings()` получил fallback чтения через `GET /items/tvpn_admin_settings/singleton`, если основной `GET /items/tvpn_admin_settings` недоступен;
    - `saveSettings()` теперь:
      - отправляет payload без `id`,
      - сначала пробует `PATCH /items/tvpn_admin_settings`,
      - затем fallback `PATCH /items/tvpn_admin_settings/singleton`,
      - и в крайнем случае `PATCH /items/tvpn_admin_settings/{id}` для legacy-сценариев;
    - улучшена диагностика ошибок: отдельный текст для network error и вывод деталей ответа API при неизвестном статусе.
  - `scripts/directus_super_setup.py`:
    - в `ensure_admin_settings()` добавлен grant `create` для Manager на `tvpn_admin_settings`;
    - в `main()` добавлен повторный `ensure_permissions_baseline(client)` сразу после `ensure_admin_settings(client)`, чтобы права гарантированно выставлялись и для коллекций, созданных поздно по ходу setup.
- Верификация:
  - `python -m py_compile scripts/directus_super_setup.py` — успешно;
  - `npm run build` в `directus/extensions/tvpn-home` — успешно;
  - `python scripts/directus_super_setup.py` — успешно;
  - `docker compose restart directus` — выполнено;
  - API smoke: `PATCH /items/tvpn_admin_settings` — `200`, rollback — `200`.

### 2026-02-13 — локальный E2E smoke (Manager + UI)

- Выполнено на локальном стенде `http://localhost:8055`:
  - создан тестовый пользователь `manager.local.test@example.com` с ролью `Manager` (active);
  - под токеном Manager проверены:
    - `GET /items/tvpn_admin_settings` -> `200`;
    - `PATCH /items/tvpn_admin_settings` -> `200`;
    - rollback сохранения -> `200`.
- UI-проверка через браузер MCP:
  - в `Directus /admin/tvpn-home` изменен `Текст для пользователей`;
  - нажата кнопка `Сохранить` в блоке "Технические работы";
  - после завершения запроса кнопка вернулась в активное состояние без error-алерта (признак успешного save).
- После UI-теста значение `maintenance_message` откатили на `probe` (чтобы не оставлять тестовый текст).

### 2026-02-16 — hotfix backend startup crash (NameError Any)

- Симптом:
  - продовый backend не поднимался, публичные endpoint (`/health`, `/app/info`) отдавали `502` через reverse-proxy;
  - в контейнерных логах повторялась ошибка запуска: `NameError: name 'Any' is not defined` в `bloobcat/routes/payment.py`.
- Корневая причина:
  - в `payment.py` использовалась аннотация `Any` (`def _meta_bool(value: Any, ...)`), но импорт `Any` из `typing` отсутствовал.
- Исправление:
  - в `TVPN_BACK_END/bloobcat/routes/payment.py` добавлен импорт `from typing import Any`.
- Проверка:
  - `python -m py_compile bloobcat/routes/payment.py` проходит без ошибок.

### 2026-02-20 — self-heal FK для notification_marks (ON DELETE CASCADE)

- Симптом:
  - удаление пользователей через Directus падало с ошибкой: `FOREIGN KEY constraint violation` на `notification_marks_user_id_fkey`.
- Причина:
  - FK constraint `notification_marks_user_id_fkey` был создан без `ON DELETE CASCADE`;
  - Aerich записал миграцию `81_20260220_fix_notification_marks_cascade.py` как "applied", но SQL на проде не выполнился;
  - из-за этого удаление cascade'ом не работало.
- Исправление:
  - в `bloobcat/__main__.py` добавлена функция `ensure_notification_marks_fk_cascade()` (строки 153–198) по аналогии с `ensure_active_tariffs_fk_cascade()`;
  - функция при старте приложения проверяет constraint через `information_schema.table_constraints / key_column_usage`;
  - если constraint существует, но без `ON DELETE CASCADE` — автоматически пересоздаёт его с CASCADE (self-heal);
  - функция вызывается в `lifespan` (строка 238) сразу после `ensure_active_tariffs_fk_cascade()`.
- Пример SQL (что исправляется):
  - было: `ALTER TABLE notification_marks ... ON DELETE NO ACTION`;
  - стало: `ALTER TABLE notification_marks ... ON DELETE CASCADE`.
- Верификация:
  - `python -m py_compile bloobcat/__main__.py` — успешно;
  - `ReadLints` по файлу — ошибок нет;
  - при старте backend логирует: `[INFO] Self-healing FK constraint notification_marks_user_id_fkey with ON DELETE CASCADE` (если пересоздание произошло).
- Файл:
  - `TVPN_BACK_END/bloobcat/__main__.py`.

### 2026-02-20 — registration gate hardening (only family/ref/qr bypass)

- **fix**: ужесточена логика авто-регистрации, чтобы новый пользователь без валидного deep-link не создавался неявно.
- Изменения:
  - добавлен модуль `bloobcat/funcs/start_params.py` с единым whitelist-предикатом `is_registration_exception_start_param(...)`;
  - в `bloobcat/routes/auth.py` `should_register` теперь true только при `registerIntent=true` или whitelist `start_param` (`family/ref/qr`);
  - в `bloobcat/funcs/validate.py`:
    - referer-fallback больше не читает `utm` как стартовый параметр;
    - создание пользователя допускается только для whitelist `start_param`, иначе `403 User not registered`.
- Эффект:
  - обычное открытие без start-пейлоада больше не может случайно обойти welcome-регистрацию;
  - deep-link сценарии `family/ref/qr` сохранены.
- Тесты:
  - `tests/test_auth_registration_modes.py`: добавлен кейс `campaign-abc` (не whitelist) -> `requires_registration=true`;
  - `tests/test_resilience_hardening.py`: добавлен кейс `validate` с не-whitelist `start_param` -> `403 User not registered`;
  - прогон: `py -3.12 -m pytest tests/test_auth_registration_modes.py tests/test_resilience_hardening.py -q` -> `13 passed`.

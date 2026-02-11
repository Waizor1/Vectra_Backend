## Журнал изменений

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


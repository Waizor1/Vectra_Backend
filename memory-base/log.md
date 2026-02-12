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


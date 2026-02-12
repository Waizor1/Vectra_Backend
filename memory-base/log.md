## Журнал изменений

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


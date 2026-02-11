## Directus UI/UX (пошаговая настройка)

Скрипт: `scripts/directus_ui_ux_stepwise.py`

Шаги:
1. Создает папки навигации (`grp_*`).
2. Группирует коллекции, назначает иконки/описания/сортировку.
3. Добавляет русские подписи и подсказки на ключевые поля.
4. Прячет служебные коллекции.

---

## Directus super-setup (рекомендуется)

Скрипт: `scripts/directus_super_setup.py`

Делает всё в одном прогоне (идемпотентно):
- Ставит язык `ru-RU` (инстанс + текущий пользователь).
- Чинит “пустую админку” через базовые permissions (Administrator + роли `Manager`/`Viewer`).
- Создает/обновляет папки навигации `grp_*`, назначает иконки/описания/сортировку.
- Добавляет подсказки и русские подписи на ключевые поля.
- Создает “Главный дашборд” в Insights и базовые панели.
- Добавляет пару удобных bookmarks (presets) по ролям.

Запуск:
- выставить `DIRECTUS_URL`, `DIRECTUS_ADMIN_EMAIL`, `DIRECTUS_ADMIN_PASSWORD`
- `python scripts/directus_super_setup.py`

## Главный экран (Home)

Реализован как модуль-расширение Directus:
- Папка: `directus/extensions/tvpn-home`
- URL: `/admin/tvpn-home`
- По умолчанию root `/` ведет на “Главную” (см. `docker-compose.yml` → `ROOT_REDIRECT`)

Модуль включает:
- карточки KPI (users / active_tariffs / blocked / payments)
- динамика за 7 дней (connections / registrations) через `/admin-widgets/*`
- быстрые действия + навигация, визуально ближе к RemnaWave/FastAdmin
- адаптивная раскладка: на широких экранах правый сайдбар (sticky) с быстрыми действиями/здоровьем/подсказками, чтобы не “пустовало” место справа

Включение в UI:
- Скрипт `scripts/directus_super_setup.py` пытается включить extension автоматически (если он уже подхвачен Directus).

## Troubleshooting: “админка пустая, разделов нет”

Чаще всего причина одна из:
- роль без permissions на коллекции (`users`, `tariffs`, `promo_*` и т.д.)
- у роли выключен `app_access` (тогда в App почти ничего не видно)
- коллекции скрыты (`meta.hidden=true`) при этом у роли доступ только к “служебным”

Быстрое исправление:
- запусти `python scripts/directus_super_setup.py`

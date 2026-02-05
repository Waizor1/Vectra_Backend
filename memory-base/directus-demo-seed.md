## Directus demo seed

Скрипты:
- `scripts/seed_directus_demo.py` — наполняет базу тестовыми данными (идемпотентно).
- `scripts/directus_set_language_ru.py` — ставит язык админки на `ru-RU`.
- `scripts/directus_ui_ux_upgrade.py` — группирует навигацию, прячет служебное, добавляет подсказки.

Что создается:
- Users: 5 тестовых пользователей.
- Tariffs: 2 тарифа.
- Active Tariffs: 2 записи (ID `AT001`, `AT002`).
- Promo: 1 batch, 2 promo codes, 1 usage.
- Prize Wheel: 3 конфигурации, 2 истории.
- Connections: 30 подключений.

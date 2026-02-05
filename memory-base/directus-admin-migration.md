## Перенос админ-аккаунтов в Directus

### Скрипт
Файл: `scripts/migrate_admins_to_directus.py`

Что делает:
- Логинится в Directus по `DIRECTUS_ADMIN_EMAIL`/`DIRECTUS_ADMIN_PASSWORD`.
- Находит роль с `admin_access=true`.
- Создает пользователей на основе таблицы `Admin`.

### Политика email/паролей
- email для Admin: `{username}@admin.local`
- пароль для импортируемых админов: `DIRECTUS_IMPORTED_ADMIN_PASSWORD`
- имя пользователя кладется в `first_name`

### Переменные окружения
- `DIRECTUS_URL` (например `http://localhost:8055`)
- `DIRECTUS_ADMIN_EMAIL`, `DIRECTUS_ADMIN_PASSWORD`
- `DIRECTUS_IMPORTED_ADMIN_PASSWORD`
- `SCRIPT_DB` (строка подключения для Tortoise)

### Важно
- Скрипт идемпотентен: пропускает пользователей, если email уже существует.
- После первого импорта рекомендуется вручную сменить пароли и при необходимости заменить email.
- При локальном запуске указывай `SCRIPT_DB=postgres://postgres:postgres@localhost:59132/postgres` (доступ к контейнерной БД с хоста).
- Скрипт использует минимальную конфигурацию Tortoise только для модели `Admin`.

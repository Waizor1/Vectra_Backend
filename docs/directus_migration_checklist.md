## Directus Migration Checklist

### Подготовка
- Заполнить переменные окружения в `.env` (DIRECTUS_*, ADMIN_INTEGRATION_*).
- Поднять `directus` сервис через `docker-compose up -d`.
- Убедиться, что Directus подключился к текущей БД.

### Настройка Directus
- Создать коллекции и связи согласно `memory-base/directus-schema-map.md`.
- Настроить роли/permissions (readonly/editable).
- Подключить extensions (hooks + endpoints).
- Запустить `python scripts/directus_post_setup.py` для readonly полей.
- Запустить `python scripts/migrate_admins_to_directus.py` для переноса админов.

### Проверка ключевых сценариев
- Users: редактирование `lte_gb_total` → синхронизация LTE/RemnaWave.
- Users: изменение `expired_at` и `hwid_limit` → RemnaWave update.
- ActiveTariffs: изменение `lte_gb_total` → синхронизация LTE.
- PromoCode: ввод `raw_code` или `code_hmac` → корректный HMAC.
- PrizeWheelConfig: валидации вероятностей и `subscription` value.
- Widgets: сверка данных графиков с FastAdmin.
- User delete: удаление из RemnaWave и локальной БД.

### Переключение
- Вести FastAdmin и Directus параллельно на время проверки.
- Зафиксировать момент переключения на Directus.
- Отключить FastAdmin после успешной валидации.

### Откат
- При ошибках вернуть FastAdmin и отключить Directus.

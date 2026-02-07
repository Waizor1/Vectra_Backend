## Directus интеграция: что добавлено

### Docker
- В `docker-compose.yml` добавлен сервис `directus` (порт 8055).
- Монтируются `./directus/extensions` и `directus-uploads`.
- Текущая версия образа Directus: `11.15.0`.

### Backend (FastAPI)
- Endpoint'ы интеграции:
  - `POST /admin/integration/users/{user_id}/sync`
  - `POST /admin/integration/active-tariffs/{active_tariff_id}/sync`
  - `DELETE /admin/integration/users/{user_id}`
- Защита токеном: `X-Admin-Integration-Token`.
- Настройки: `ADMIN_INTEGRATION_TOKEN` в `.env`.

### Directus extensions
- Hooks:
  - `remnawave-sync`: вызывает backend при изменениях Users/ActiveTariffs и удалении Users.
  - `promo-code-hmac`: генерирует HMAC для промокодов.
  - `prize-wheel-validate`: валидирует вероятности и `subscription`-значения.
- Endpoints:
  - `admin-widgets`: графики `/admin-widgets/*` с периодами.
- Формат локальных расширений:
  - `directus/extensions/<ext>/package.json` с `directus:extension` (`type`, `path`, `source`, `host`).
  - `src/index.js` и `dist/index.js` в каждом расширении.

### Переменные окружения (см. .env.example)
- `DIRECTUS_*`, `ADMIN_INTEGRATION_*`, `PROMO_HMAC_SECRET`.

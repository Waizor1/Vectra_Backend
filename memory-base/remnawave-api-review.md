## RemnaWave API (v2.6.0) — проверка интеграции

Дата: 2026-02-07

### Документация
- Файл: `api-1.json` (OpenAPI 3.0, заголовок: **Remnawave API v2.6.0**).

### Эндпоинты панели, используемые в коде
- Пользователи:
  - `POST /api/users` — create (обязательные поля: `username`, `expireAt`)
  - `PATCH /api/users` — update (приоритет `uuid`, поля `expireAt`, `hwidDeviceLimit`, `activeInternalSquads`, `externalSquadUuid`)
  - `GET /api/users` — список (`size`, `start`)
  - `GET /api/users/{uuid}` — получение по UUID
  - `DELETE /api/users/{uuid}` — удаление
  - `POST /api/users/{uuid}/actions/revoke` — revoke
- HWID:
  - `GET /api/hwid/devices` — все устройства
  - `GET /api/hwid/devices/{userUuid}` — устройства пользователя
  - `POST /api/hwid/devices` — добавить устройство (требует `userUuid`, `hwid`)
  - `POST /api/hwid/devices/delete` — удалить устройство
- Трафик/статистика:
  - `GET /api/bandwidth-stats/users/{uuid}/legacy`
  - `GET /api/bandwidth-stats/nodes/{uuid}/users/legacy`
  - `GET /api/bandwidth-stats/nodes?start&end&topNodesLimit`
- Инбаунды:
  - `GET /api/config-profiles/inbounds`
- Сервисные:
  - `POST /api/system/tools/happ/encrypt`

### Итог проверки
- Эндпоинты и имена полей соответствуют документации.
- `subscriptionUrl` есть в ответе пользователя; поле `happ.cryptoLink` не описано в схеме, fallback через `/api/system/tools/happ/encrypt` остаётся корректным.
- Изменений в коде не потребовалось.

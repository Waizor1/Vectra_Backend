# BloobCat Backend – Captain User Lookup

Captain User Lookup — это защищённый HTTPS-endpoint FastAPI, встраиваемый в существующий backend. Он принимает Telegram ID и возвращает все известные поля пользователя, включая оперативные данные из панели RemnaWave. Доступ контролируется обязательным Bearer API-ключом и allowlist доменов.

## Установка и запуск

1. Установите зависимости:
   ```bash
   cd Blubcat_BACK_END
   poetry install
   ```
2. Скопируйте пример окружения и задайте собственные значения API-ключа и списка разрешённых доменов:
   ```bash
   cp .env.captain-user-lookup.example .env.captain-user-lookup
   ```
   Добавьте содержимое файла в основной `.env`, который уже используется приложением.
3. Запустите сервер (поднимает Telegram-бота и HTTP API, включая Captain User Lookup):
   ```bash
   poetry run python -m bloobcat
   ```

## Переменные окружения

Captain User Lookup использует два параметра:

```
API_KEY=super-secret-api-key
ALLOWLIST_DOMAINS=api.example.com,admin.example.com
```

- `API_KEY` — значение, с которым должен совпадать Bearer-токен в заголовке `Authorization`.
- `ALLOWLIST_DOMAINS` — запятая-разделённый список доменов без схемы. Если список пуст, проверка отключена.

Пример с этими переменными доступен в файле `.env.captain-user-lookup.example`.

## Captain User Lookup API

- **Метод**: `GET https://<ваш-домен>/api/users/{telegram_id}`
- **Аутентификация**: `Authorization: Bearer <API_KEY>`
- **Allowlist**: запросы принимаются только с доменов, присутствующих в `ALLOWLIST_DOMAINS`.

### Параметры

| Имя | Где | Тип | Описание |
| --- | --- | --- | --- |
| `telegram_id` | Path | int | Целое число > 0. Используется для поиска пользователя. |
| `Authorization` | Header | string | Bearer-токен. Обязателен. |

### Возможные ответы

| Код | Тело | Когда возвращается |
| --- | --- | --- |
| `200` | Полный профиль пользователя | Пользователь найден и проверки пройдены |
| `400` | `{ "error": "invalid_telegram_id" }` | ID передан не как положительное целое |
| `401` | `{ "error": "unauthorized" }` | API-ключ отсутствует или неверен |
| `403` | `{ "error": "forbidden" }` | Хост запроса не в allowlist |
| `404` | `{ "error": "not_found" }` | Пользователь отсутствует в хранилище |

### Структура `200 OK`

```json
{
  "telegram_id": 101010101,
  "first_name": "Captain",
  "last_name": "Demo",
  "username": "captain_demo",
  "email": "captain.demo@example.com",
  "phone": null,
  "country": "RU",
  "status": "active",
  "active_subscriptions": [
    {
      "name": "BloobCat Premium",
      "status": "active",
      "months": 12,
      "price": 1290,
      "started_at": "2024-07-01T12:00:00",
      "expires_at": "2024-08-30T12:00:00"
    },
    {
      "name": "Payment 90342",
      "status": "succeeded",
      "months": null,
      "price": 1290,
      "started_at": "2024-06-25T10:15:00",
      "expires_at": null
    }
  ],
  "balance": 42.5,
  "registered_at": "2023-07-01T12:00:00",
  "last_login": "2024-07-26T09:00:00",
  "remnawave": {
    "uuid": "7d0f6f97-3de9-4250-9b37-92f83a3d11ac",
    "username": "101010101",
    "status": "ACTIVE",
    "expire_at": "2024-08-30T00:00:00+00:00",
    "online_at": "2024-07-26T09:00:00+00:00",
    "hwid_limit": 3,
    "traffic_limit_bytes": 0,
    "subscription_url": "https://panel.example.com/link/abcd",
    "telegram_id": 101010101,
    "email": "captain.demo@example.com",
    "active_internal_squads": [
      "default-squad"
    ],
    "devices": [
      {
        "hwid": "abcd-1234",
        "user_uuid": "7d0f6f97-3de9-4250-9b37-92f83a3d11ac",
        "platform": "android",
        "os_version": "14",
        "device_model": "Pixel 7",
        "user_agent": "org.telegram.messenger/10.2",
        "created_at": "2024-07-01T12:00:00",
        "updated_at": "2024-07-26T09:00:00"
      }
    ]
  }
}
```

### Пример запроса

```bash
curl \
  -H "Authorization: Bearer super-secret-api-key" \
  https://api.example.com/api/users/101010101
```

### Логирование и безопасность

- Логи содержат только Telegram ID и статус ответа (персональные данные не записываются).
- Запрос отклоняется, если домен не входит в allowlist.
- Для каждого запроса дополнительно выполняется вызов RemnaWave API (если у пользователя есть UUID); ошибки панели не ломают ответ — поле `remnawave` вернёт `null`.

### Откуда берутся данные

- `telegram_id`, `username`, `full_name`, `email`, `balance`, `registration_date`, `connected_at`, `is_trial`, `expired_at`, `is_subscribed`, `is_blocked` — поля модели `bloobcat.db.users.Users`.
- `first_name`/`last_name` — разбивка `full_name` по первому пробелу.
- `phone` — в текущей схеме не хранится, поэтому возвращается `null`.
- `country` — маппинг кода языка (`language_code`) пользователя на ISO страну.
- `status` — рассчитывается из `is_blocked`, `is_trial` и `expired_at` (`blocked`, `trial_active`, `trial_expired`, `active`, `expired`, `new`).
- `active_subscriptions` — первый элемент описывает действующий тариф (`ActiveTariffs`), дополнительные элементы — последние успешные платежи (`ProcessedPayments`).
- `remnawave` — снэпшот данных из панели через `RemnaWaveClient.get_user_by_uuid` (UUID, статус, даты, лимиты, squad-ы, crypto link) плюс список устройств из `GET /api/hwid/devices/{uuid}`. При ошибках обращение логируется, поле выставляется в `null`.

# BloobCat Backend – Captain User Lookup

Captain User Lookup — это защищённый HTTPS-endpoint FastAPI, встраиваемый в существующий backend. Он принимает Telegram ID и возвращает все известные поля пользователя. Доступ контролируется обязательным Bearer API-ключом и allowlist доменов.

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
  "phone": "+1234567890",
  "country": "US",
  "status": "active",
  "active_subscriptions": [
    {
      "name": "BloobCat Premium",
      "status": "active",
      "started_at": "2024-07-01T12:00:00",
      "expires_at": "2024-08-30T12:00:00"
    }
  ],
  "balance": 42.5,
  "registered_at": "2023-07-01T12:00:00",
  "last_login": "2024-07-26T09:00:00"
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
- Mock-хранилище пользователей можно заменить на реальный репозиторий.

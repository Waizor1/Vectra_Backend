## Тесты: запуск и окружение

Дата: 2026-02-07

### Окружение
- Для тестов используется Python 3.12 (установлен через winget).
- Команда запуска: `py -3.12 -m pytest`

### Интеграционные тесты (по умолчанию пропускаются)
- `test_statistics.py`:
  - Включить: `RUN_STATISTICS_TESTS=1`
- `bloobcat/test_notifications.py`:
  - Включить: `RUN_NOTIFICATION_TESTS=1`
  - Требуются переменные:
    - `TEST_NOTIFICATION_USER_ID`
    - `TEST_NOTIFICATION_USER_IDS` (через запятую)

### Примечания
- В тестах `tests/test_payments_no_yookassa.py` используется custom генерация схемы SQLite
  без проверки циклических FK.

### Рефералка: важный кейс
- Пользователь может уже существовать в БД (например, ранее нажал `/start` без реф-ссылки), а затем открыть Mini App по реф-ссылке.
  Если `referred_by` выставлять только для "совсем новых" пользователей, рефералка не сработает при оплате (не будет +7 дней другу и бонусов рефереру).

### Версия и время сборки
- `GET /health` и `GET /app/info` возвращают поля `version` и `build_time`.
- В Docker можно зафиксировать "время сборки" через build-arg:
  - PowerShell пример: `$env:BUILD_TIME = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")`
  - Затем: `docker compose build --no-cache bloobcat`

### 2026-02-20: activation/HWID checker hardening (prod)
- Цель: восстановить диагностику и отказоустойчивость для кейсов
  - нет notify при первой активации,
  - не детектится duplicate HWID.
- Изменения в `bloobcat/routes/remnawave/catcher.py`:
  - добавлены `info`-метрики входа checker-а: `total_users`, `users_with_uuid_and_expired`;
  - добавлен summary по циклу: `onlineAt_available`, `recovered_onlineAt`, `activation_notify_sent`, `duplicate_hwid_detected`, `duplicate_hwid_blocked`, `duplicate_hwid_paid_skip`;
  - добавлены reason-логи skip активации с флагами (`is_registered`, `block_registration`, `is_antitwink_sanction`, `has_paid_subscription`, `old_connected_at`);
  - добавлен safe parser `_safe_parse_online_at()` (не роняет цикл при нестандартном timestamp).
- Изменения в `bloobcat/routes/remnawave/hwid_utils.py`:
  - `parse_remnawave_devices()` усилен fail-safe разбором payload-форматов (`items`, `rows`, `result`, вложенные dict);
  - добавлен fallback на одиночный dict-объект устройства (`hwid/deviceId/id`), чтобы не терять устройство из-за формата ответа.
- Регрессионные тесты:
  - `tests/test_remnawave_activation.py`:
    - `test_parse_remnawave_devices_dict_response_items`
    - `test_parse_remnawave_devices_single_device_dict_failsafe`
    - `test_safe_parse_online_at_invalid_format_returns_none`
    - `test_safe_parse_online_at_valid_iso_returns_datetime`
- Команда проверки:
  - `py -3.12 -m pytest tests/test_remnawave_activation.py tests/test_hwid_antitwink.py tests/test_connections_process.py -q`
  - результат: `41 passed`.

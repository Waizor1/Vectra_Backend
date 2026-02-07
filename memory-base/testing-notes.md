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

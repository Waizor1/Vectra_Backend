## Переезд FastAdmin -> Directus (сводка)

Дата: 2026-02-04

### Текущее состояние админки (FastAdmin)
- FastAdmin монтируется в `bloobcat/__main__.py` по `/admin`, есть патч разрешений.
- Админ-аккаунты: `bloobcat/db/admins.py` (bcrypt, is_superuser, is_active).
- Основные модели админки:
  - `Users` — `bloobcat/db/users.py` (много полей, кастомный `save_model` с RemnaWave).
  - `Tariffs` — `bloobcat/db/tariff.py` (editable поля).
  - `PromoBatch`, `PromoCode`, `PromoUsage` — `bloobcat/db/promotions.py` (HMAC).
  - `PrizeWheelHistory`, `PrizeWheelConfig` — `bloobcat/db/prize_wheel.py` (валидация сумм вероятностей).
  - `ActiveTariffs` — `bloobcat/db/active_tariff.py` (LTE логика, RemnaWave).
- Дашбордные виджеты: `bloobcat/admin/*.py` (5 графиков, raw SQL).
- Телеграм-админка отдельная: `bloobcat/bot/routes/admin/`.

### Что потребуется в Directus
- Коллекции: users, tariffs, promo_*, prize_wheel_*, active_tariffs, admins (в Directus Users/Role).
- Поля readonly/editable настроить на уровне permissions.
- Хуки/flows:
  - синхронизация RemnaWave при изменениях Users/ActiveTariffs;
  - HMAC генерация для PromoCode;
  - валидация сумм вероятностей для PrizeWheelConfig.
- Кастомные эндпоинты для 5 графиков дашборда.
- Роли/права: аналог is_superuser, field-level permissions.

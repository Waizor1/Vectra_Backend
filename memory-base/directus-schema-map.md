## Directus: схема, роли, права (проектирование)

Дата: 2026-02-04

### Коллекции (mapping из текущих таблиц)
- `users` (из `Users`)
  - Связи: `active_tariff` -> `active_tariffs.id` (FK)
  - Readonly: `id`, `registration_date`, `activation_date`, `referrals`, `username`, `full_name`
  - Editable: `prize_wheel_attempts`, `lte_gb_total`, `hwid_limit`, `expired_at`, `balance`, `is_partner`, `is_blocked`, `blocked_at`, `custom_referral_percent`
- `active_tariffs` (из `ActiveTariffs`)
  - Связи: `user` -> `users.id`
  - Readonly: все, кроме `lte_gb_total`
  - Editable: `lte_gb_total`
- `tariffs` (из `Tariffs`)
  - Editable: `order`, `is_active`, `family_plan_enabled`, `final_price_default`, `final_price_family`, `devices_limit_default`, `devices_limit_family`, `base_price`, `progressive_multiplier`, `lte_enabled`, `lte_price_per_gb`, `name`, `months`
- `promo_batches` (из `PromoBatch`)
  - Readonly: `id`, `created_at`
  - Связи: `created_by` -> directus_users.id (если хотим аудит)
- `promo_codes` (из `PromoCode`)
  - Readonly: `id`, `created_at`
  - Специфика: ввод «сырого кода» -> генерация `code_hmac`
- `promo_usages` (из `PromoUsage`)
  - Readonly: `id`, `used_at`
  - Связи: `promo_code` -> `promo_codes.id`, `user` -> `users.id`
- `prize_wheel_config` (из `PrizeWheelConfig`)
  - Readonly: `id`, `created_at`, `updated_at`
  - Валидации: диапазон вероятностей 0..1, сумма <= 1.0, `subscription` -> `prize_value` целое > 0
- `prize_wheel_history` (из `PrizeWheelHistory`)
  - Readonly: `id`, `created_at`
- `connections` (из `Connections`)
  - Readonly: все (для дашборда)
- `notification_marks`, `personal_discounts`, `hwid_devices_local`, `processed_payments`
  - Readonly по умолчанию, открывать только при необходимости (в отдельной роли).

### Роли Directus
- `Admin` (полный доступ + управление ролями)
- `Manager` (CRUD на `users`, `active_tariffs`, `tariffs`, `promo_*`, `prize_wheel_*`; readonly системные таблицы)
- `Viewer` (read-only на основную витрину, без промо/изменений)

### Политика прав (field-level)
- `users`:
  - Только read: `id`, `registration_date`, `activation_date`, `referrals`, `username`, `full_name`
  - Write: `prize_wheel_attempts`, `lte_gb_total`, `hwid_limit`, `expired_at`, `balance`, `is_partner`, `is_blocked`, `blocked_at`, `custom_referral_percent`
- `active_tariffs`:
  - Write: `lte_gb_total`
  - Readonly: остальные поля (ID, цены, остатки, usage)
- `promo_codes`:
  - Разрешить ввод `code_hmac` (hook обрабатывает raw/hex)

### Дашбордные метрики
Для Directus endpoints:
- total_users
- active_users
- inactive_users
- registered_users
- connections

### Безопасность интеграции
- Ввести отдельный токен `ADMIN_INTEGRATION_TOKEN` для защищенных endpoint'ов в backend.
- Directus вызывает backend только с этим токеном.

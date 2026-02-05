## Инвентаризация админ-функционала (FastAdmin -> Directus)

Дата: 2026-02-04

### Где смонтирован FastAdmin
- `/admin` в [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\__main__.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\__main__.py)
- Патч прав на add/change/delete: только суперюзеры (см. `only_superuser`).

### Админ-учетки (FastAdmin)
- Модель: `Admin` в [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\admins.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\admins.py)
- Поля: `username`, `hash_password`, `is_superuser`, `is_active`
- Аутентификация: bcrypt
- Права: add/change/delete только `is_superuser`

### Модели, доступные в FastAdmin (CRUD)

#### Users
- Файл: [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\users.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\users.py)
- `list_editable`: `prize_wheel_attempts`
- `readonly_fields`: `id`, `registration_date`, `activation_date`, `referrals`, `username`, `full_name`
- `fields`: id, username, full_name, expired_at, is_registered, balance, referred_by, is_admin, is_partner, custom_referral_percent, registration_date, referrals, is_subscribed, utm, renew_id, connected_at, email, created_at, is_trial, used_trial, remnawave_uuid, familyurl, active_tariff, lte_gb_total, hwid_limit, is_blocked, blocked_at, last_failed_message_at, failed_message_count, prize_wheel_attempts
- Кастомная логика `save_model`: синхронизация LTE и RemnaWave (обновление `ActiveTariffs.lte_gb_total`, `set_lte_squad_status`, чистка `NotificationMarks`).

#### ActiveTariffs
- Файл: [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\active_tariff.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\active_tariff.py)
- `list_editable`: `lte_gb_total`
- `readonly_fields`: все кроме `lte_gb_total`
- Кастомная логика `save_model`: пересчет доступа LTE, `set_lte_squad_status`, чистка `NotificationMarks`.

#### Tariffs
- Файл: [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\tariff.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\tariff.py)
- `list_editable`: `order`, `progressive_multiplier`, `lte_enabled`, `lte_price_per_gb`

#### Promotions
- Файл: [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\promotions.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\promotions.py)
- PromoBatch: readonly `id`, `created_at`
- PromoCode: custom schema `raw_code` -> HMAC, `save_model` генерирует `code_hmac`
- PromoUsage: readonly `id`, `used_at`

#### Prize Wheel
- Файл: [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\prize_wheel.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\prize_wheel.py)
- PrizeWheelConfig: `save_model` валидирует сумму вероятностей <= 1.0 и тип `subscription`
- PrizeWheelHistory: readonly `id`, `created_at`

### Dashboard-виджеты (5 графиков)
- Файлы: [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\admin\*.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\admin\)
- Все используют raw SQL (generate_series) и период `day|week|month|year`
- Виджеты:
  - `total_users_widget.py`
  - `active_users_widget.py`
  - `inactive_users_widget.py`
  - `registered_users_widget.py`
  - `connections_widget.py`

### Таблицы, которые используются в виджетах/логике
- `users` (модель Users)
- `connections` (модель Connections в [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\connections.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\connections.py))
- `notification_marks` (для удаления меток LTE)

### Модели в БД без FastAdmin UI (но важны для полноты)
- `PersonalDiscount` — [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\discounts.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\discounts.py)
- `HwidDeviceLocal` — [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\hwid_local.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\hwid_local.py)
- `NotificationMarks` — [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\notifications.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\notifications.py)
- `ProcessedPayments` — [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\payments.py](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\db\payments.py)

### Внешние интеграции, затрагиваемые админкой
- RemnaWave: синхронизация LTE статуса и usage
- Promo HMAC: `PROMO_HMAC_SECRET`
- Telegram admin маршруты — отдельный контур (не FastAdmin UI): [C:\Users\user\Documents\TVPN_BACK_END\bloobcat\bot\routes\admin\](C:\Users\user\Documents\TVPN_BACK_END\bloobcat\bot\routes\admin\)

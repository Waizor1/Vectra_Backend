## Рулетка призов и промокоды — примеры добавления

Ниже — практичные примеры, как настроить призы для колеса и как создавать промокоды всех типов, включая комбинированные. Примеры основаны на текущей логике бэкенда.

### Типы призов колеса

- **subscription**: продление подписки на N дней. `prize_value` — количество дней (целое число > 0).
- **extra_spin**: дополнительная попытка кручения. `prize_value` — строка (например, "1").
- **discount_percent**: персональная скидка для пользователя. `prize_value` — строка формата:
  - "15" — скидка 15% на 1 использование;
  - "15:perm" или "15:permanent" — бессрочная скидка 15% (uses=0);
  - "15:uses=2" — скидка 15% на 2 использования;
  - "15:exp=2025-12-31" — скидка 15% с датой окончания;
  - можно комбинировать: "15:uses=3:exp=2025-12-31".
- **material_prize**: материальный приз. Требует участия админа: установите `requires_admin=true`. `prize_value` — произвольное описание (например, размер/цвет/условия выдачи).
- **nothing**: пустышка. В БД не хранится; выпадает автоматически, если сумма вероятностей активных призов < 1.0.

Важно:
- Сумма `probability` по активным призам должна быть ≤ 1.0. Остаток — это вероятность "nothing".
- Уникальность теперь по паре (`prize_type`, `prize_value`), что позволяет несколько призов одного типа (например, несколько подписок с разной длительностью).

### Добавление призов через FastAdmin

Раздел: `Конфигурация призов` (модель `PrizeWheelConfig`). Поля формы:
- **prize_type**: `subscription` | `extra_spin` | `discount_percent` | `material_prize`
- **prize_name**: отображаемое название приза
- **prize_value**: значение (смотри примеры ниже)
- **probability**: число от 0 до 1 (вероятность выпадения)
- **is_active**: активировать приз
- **requires_admin**: включить, если приз требует ручной выдачи админом

Примеры значений для каждого типа:
- **subscription** (продление на 7 дней)
  - prize_type: `subscription`
  - prize_name: `Подписка (7 дней)`
  - prize_value: `7`
  - probability: `0.05`
  - is_active: `true`
  - requires_admin: `false`

- **extra_spin** (доп. попытка)
  - prize_type: `extra_spin`
  - prize_name: `Еще одна попытка`
  - prize_value: `1`
  - probability: `0.20`
  - is_active: `true`
  - requires_admin: `false`

- **discount_percent** (варианты формата prize_value):
  - `15` — скидка 15% (1 использование)
  - `20:perm` — скидка 20% навсегда (remaining_uses=0)
  - `10:uses=3` — скидка 10% на 3 использования
  - `10:uses=3:exp=2025-12-31` — скидка 10% ×3 до даты
  Пример записи:
  - prize_type: `discount_percent`
  - prize_name: `Скидка 15%`
  - prize_value: `15`
  - probability: `0.10`
  - is_active: `true`
  - requires_admin: `false`

- **material_prize** (ручная выдача)
  - prize_type: `material_prize`
  - prize_name: `Футболка`
  - prize_value: `Размер M` (любое описание)
  - probability: `0.01`
  - is_active: `true`
  - requires_admin: `true` (обязательно, чтобы админ подтвердил выдачу)

Замечания:
- Для `subscription` валидируется, что `prize_value` — целое число > 0.
- Если сумма вероятностей активных призов > 1.0 — сохранение не пройдет.
- Тип `nothing` отдельно не добавляется — это остаток вероятности.

### Создание промокодов через FastAdmin

Разделы: `Партии промокодов` (опционально) и `Промокоды`.

Форма создания (`Промокоды` → Создать):
- **batch_id**: (опционально) выберите партию
- **name**: человекочитаемое имя кода
- **raw_code**: исходный код (HMAC сгенерируется автоматически)
- **effects**: JSON с эффектами (см. примеры ниже)
- **max_activations**: общий лимит активаций
- **per_user_limit**: лимит на пользователя
- **expires_at**: дата истечения кода (опционально)
- **disabled**: выключить код

Требование: должен быть настроен секрет `PROMO_HMAC_SECRET` в конфиге, иначе создание по `raw_code` не пройдет.

Примеры JSON для поля effects:
- Продление подписки на 30 дней:
```json
{ "extend_days": 30 }
```
- Плюс одно устройство (HWID):
```json
{ "add_hwid": 1 }
```
- Скидка 20% на 2 использования до даты:
```json
{ "discount_percent": 20, "uses": 2, "discount_expires_at": "2025-12-31" }
```
- Скидка 20% навсегда:
```json
{ "discount_percent": 20, "permanent": true }
```
- Комбинированный код (15 дней + 1 HWID + 10% ×2):
```json
{ "extend_days": 15, "add_hwid": 1, "discount_percent": 10, "uses": 2 }
```

После создания код можно валидировать/активировать с клиента через эндпоинты `/promo/validate` и `/promo/redeem`.

## Приложение: API/SQL (необязательно)

### Как посмотреть/инициализировать конфиг через API

```bash
curl -sS -X GET http://localhost:8000/prize-wheel/config | jq
```

```bash
curl -sS -X POST http://localhost:8000/prize-wheel/initialize | jq
```

### Добавление призов через SQL (PostgreSQL)

Таблица: `prize_wheel_config` (поля: `prize_type`, `prize_name`, `prize_value`, `probability`, `is_active`, `requires_admin`).

- **subscription (7 дней)**

```sql
INSERT INTO prize_wheel_config (prize_type, prize_name, prize_value, probability, is_active, requires_admin)
VALUES ('subscription', 'Подписка (7 дней)', '7', 0.05, TRUE, FALSE)
ON CONFLICT (prize_type, prize_value) DO UPDATE SET
  prize_name=EXCLUDED.prize_name,
  probability=EXCLUDED.probability,
  is_active=EXCLUDED.is_active,
  requires_admin=EXCLUDED.requires_admin;
```

- **extra_spin (доп. попытка)**

```sql
INSERT INTO prize_wheel_config (prize_type, prize_name, prize_value, probability, is_active, requires_admin)
VALUES ('extra_spin', 'Еще одна попытка', '1', 0.20, TRUE, FALSE)
ON CONFLICT (prize_type, prize_value) DO UPDATE SET
  prize_name=EXCLUDED.prize_name,
  probability=EXCLUDED.probability,
  is_active=EXCLUDED.is_active,
  requires_admin=EXCLUDED.requires_admin;
```

- **discount_percent (пример: 15% один раз)**

```sql
INSERT INTO prize_wheel_config (prize_type, prize_name, prize_value, probability, is_active, requires_admin)
VALUES ('discount_percent', 'Скидка 15%', '15', 0.10, TRUE, FALSE)
ON CONFLICT (prize_type, prize_value) DO UPDATE SET
  prize_name=EXCLUDED.prize_name,
  probability=EXCLUDED.probability,
  is_active=EXCLUDED.is_active,
  requires_admin=EXCLUDED.requires_admin;
```

- **discount_percent (пример: 20% perm)**

```sql
INSERT INTO prize_wheel_config (prize_type, prize_name, prize_value, probability, is_active, requires_admin)
VALUES ('discount_percent', 'Скидка 20% навсегда', '20:perm', 0.03, TRUE, FALSE)
ON CONFLICT (prize_type, prize_value) DO UPDATE SET
  prize_name=EXCLUDED.prize_name,
  probability=EXCLUDED.probability,
  is_active=EXCLUDED.is_active,
  requires_admin=EXCLUDED.requires_admin;
```

- **discount_percent (пример: 10% uses=3 exp=2025-12-31)**

```sql
INSERT INTO prize_wheel_config (prize_type, prize_name, prize_value, probability, is_active, requires_admin)
VALUES ('discount_percent', 'Скидка 10% ×3 до 2025-12-31', '10:uses=3:exp=2025-12-31', 0.06, TRUE, FALSE)
ON CONFLICT (prize_type, prize_value) DO UPDATE SET
  prize_name=EXCLUDED.prize_name,
  probability=EXCLUDED.probability,
  is_active=EXCLUDED.is_active,
  requires_admin=EXCLUDED.requires_admin;
```

- **material_prize (пример: Футболка M)**

```sql
INSERT INTO prize_wheel_config (prize_type, prize_name, prize_value, probability, is_active, requires_admin)
VALUES ('material_prize', 'Футболка', 'Размер M', 0.01, TRUE, TRUE)
ON CONFLICT (prize_type, prize_value) DO UPDATE SET
  prize_name=EXCLUDED.prize_name,
  probability=EXCLUDED.probability,
  is_active=EXCLUDED.is_active,
  requires_admin=EXCLUDED.requires_admin;
```

Примечание: для `material_prize` или любого приза с `requires_admin=true` выдача происходит после подтверждения админом в Telegram.

### Промокоды: типы эффектов и создание

Модель хранит только HMAC кода (`code_hmac`), сам код в БД не попадает. Эффекты — в поле `effects` (JSON). Обрабатываются:

- **extend_days**: int > 0 — продлить подписку на N дней.
- **add_hwid**: int > 0 — увеличить лимит устройств.
- **discount_percent**: int > 0 — создать персональную скидку; дополнительные флаги в `effects`:
  - `permanent` или `is_permanent`: true/false;
  - `uses`: int (если permanent=true, uses=0);
  - `discount_expires_at` или `expires_at`: дата в формате YYYY-MM-DD.
 - **add_prize_wheel_attempts**: int > 0 — добавить попытки на колесе призов пользователю.

Ограничения использования кода задаются на уровне записи:
- `max_activations` — общий лимит активаций;
- `per_user_limit` — лимит активаций на пользователя;
- `expires_at` — дата истечения самого промокода;
- `disabled` — выключить код.

#### Создание через админ-панель

В форме создания можно указать человекочитаемое имя, исходный код (будет захеширован в HMAC автоматически), JSON `effects`, лимиты и сроки.

Пример `effects`:

```json
{ "extend_days": 30 }
```

```json
{ "add_hwid": 1 }
```

```json
{ "discount_percent": 20, "uses": 2, "discount_expires_at": "2025-12-31" }
```

```json
{ "discount_percent": 20, "permanent": true }
```

```json
{ "extend_days": 15, "add_hwid": 1, "discount_percent": 10, "uses": 2 }
```

- Добавить +1 попытку колеса призов:
```json
{ "add_prize_wheel_attempts": 1 }
```

#### Создание напрямую через SQL

Сначала вычислите HMAC кода (секрет — `PROMO_HMAC_SECRET`). Пример в shell:

```bash
RAW_CODE="WELCOME2025"; echo -n "$RAW_CODE" | openssl dgst -sha256 -hmac "$PROMO_HMAC_SECRET" | awk '{print $2}'
```

Затем вставьте запись в `promo_codes` (замените `<CODE_HMAC>` на сгенерированное значение):

- **extend_days: 30**

```sql
INSERT INTO promo_codes (batch_id, name, code_hmac, effects, max_activations, per_user_limit, expires_at, disabled)
VALUES (NULL, 'Продление 30 дней', '<CODE_HMAC>', '{"extend_days": 30}', 100, 1, NULL, FALSE);
```

- **add_hwid: 1**

```sql
INSERT INTO promo_codes (batch_id, name, code_hmac, effects, max_activations, per_user_limit, expires_at, disabled)
VALUES (NULL, 'Плюс 1 устройство', '<CODE_HMAC>', '{"add_hwid": 1}', 100, 1, NULL, FALSE);
```

- **discount_percent: 20, uses=2, discount_expires_at=2025-12-31**

```sql
INSERT INTO promo_codes (batch_id, name, code_hmac, effects, max_activations, per_user_limit, expires_at, disabled)
VALUES (NULL, 'Скидка 20% ×2 до 2025-12-31', '<CODE_HMAC>', '{"discount_percent": 20, "uses": 2, "discount_expires_at": "2025-12-31"}', 100, 1, NULL, FALSE);
```

- **discount_percent: 20 permanent**

```sql
INSERT INTO promo_codes (batch_id, name, code_hmac, effects, max_activations, per_user_limit, expires_at, disabled)
VALUES (NULL, 'Скидка 20% навсегда', '<CODE_HMAC>', '{"discount_percent": 20, "permanent": true}', 100, 1, NULL, FALSE);
```

- **Комбинированный: 15 дней + 1 HWID + 10% (uses=2)**

```sql
INSERT INTO promo_codes (batch_id, name, code_hmac, effects, max_activations, per_user_limit, expires_at, disabled)
VALUES (NULL, '15 дней + HWID + 10% ×2', '<CODE_HMAC>', '{"extend_days": 15, "add_hwid": 1, "discount_percent": 10, "uses": 2}', 100, 1, NULL, FALSE);
```

- **Добавить +1 попытку колеса призов**

```sql
INSERT INTO promo_codes (batch_id, name, code_hmac, effects, max_activations, per_user_limit, expires_at, disabled)
VALUES (NULL, 'Доп. попытка колеса (+1)', '<CODE_HMAC>', '{"add_prize_wheel_attempts": 1}', 100, 1, NULL, FALSE);
```

### Проверка и активация промокода через API

```bash
curl -sS -X POST http://localhost:8000/promo/validate \
  -H "Content-Type: application/json" \
  -d '{"code":"WELCOME2025"}' | jq
```

```bash
curl -sS -X POST http://localhost:8000/promo/redeem \
  -H "Content-Type: application/json" \
  -d '{"code":"WELCOME2025"}' | jq
```

Ответы включают `effects`, остатки активаций и причины отказа (если код недействителен).



from __future__ import annotations

import os

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} not set")
    return value


def main() -> None:
    if load_dotenv:
        load_dotenv()

    base_url = env("DIRECTUS_URL")
    email = env("DIRECTUS_ADMIN_EMAIL")
    password = env("DIRECTUS_ADMIN_PASSWORD")

    session = requests.Session()
    login = session.post(f"{base_url}/auth/login", json={"email": email, "password": password}, timeout=15)
    login.raise_for_status()
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    group_defs = [
        {"collection": "grp_main", "label": "Основное", "icon": "dashboard", "sort": 1},
        {"collection": "grp_promo", "label": "Промо", "icon": "confirmation_number", "sort": 2},
        {"collection": "grp_partners", "label": "Партнерка", "icon": "handshake", "sort": 4},
        {"collection": "grp_analytics", "label": "Аналитика", "icon": "timeline", "sort": 5},
        {"collection": "grp_payments", "label": "Платежи", "icon": "payments", "sort": 6},
        {"collection": "grp_service", "label": "Служебное", "icon": "build", "sort": 7},
    ]

    for group in group_defs:
        collection = group["collection"]
        resp = session.get(f"{base_url}/collections/{collection}", headers=headers, timeout=15)
        if resp.status_code == 200:
            payload_meta = {
                "icon": group["icon"],
                "note": group["label"],
                "sort": group["sort"],
                "collapse": "open",
                "translations": [{"language": "ru-RU", "translation": group["label"]}],
            }
            session.patch(
                f"{base_url}/collections/{collection}",
                headers=headers,
                json={"meta": payload_meta},
                timeout=15,
            ).raise_for_status()
            continue

        create_payload = {
            "collection": collection,
            "schema": None,
            "meta": {
                "icon": group["icon"],
                "note": group["label"],
                "sort": group["sort"],
                "collapse": "open",
                "translations": [{"language": "ru-RU", "translation": group["label"]}],
            },
        }
        created = session.post(f"{base_url}/collections", headers=headers, json=create_payload, timeout=15)
        created.raise_for_status()

    collection_meta_updates = {
        "users": {
            "group": "grp_main",
            "icon": "people",
            "note": "Пользователи и параметры подписки",
            "sort": 1,
            "display_template": "{{username}} — {{full_name}}",
            "translations": [{"language": "ru-RU", "translation": "Пользователи"}],
        },
        "active_tariffs": {
            "group": "grp_main",
            "icon": "subscriptions",
            "note": "Текущие активные тарифы",
            "sort": 2,
            "translations": [{"language": "ru-RU", "translation": "Активные тарифы"}],
        },
        "tariffs": {
            "group": "grp_main",
            "icon": "sell",
            "note": "Тарифы для конструктора. Основная настройка — модуль Tariff Studio (/admin/tvpn-tariff-studio); raw collection оставлена как advanced/fallback.",
            "sort": 3,
            "translations": [{"language": "ru-RU", "translation": "Тарифы"}],
        },
        "promo_batches": {
            "group": "grp_promo",
            "icon": "inventory_2",
            "note": "Партии промокодов для группировки и аудита. Создайте партию перед добавлением кодов.",
            "sort": 1,
            "translations": [{"language": "ru-RU", "translation": "Партии промокодов"}],
        },
        "promo_codes": {
            "group": "grp_promo",
            "icon": "confirmation_number",
            "note": "Промокоды и эффекты. Введите сырой код — хук преобразует в HMAC.",
            "sort": 2,
            "translations": [{"language": "ru-RU", "translation": "Промокоды"}],
        },
        "promo_usages": {
            "group": "grp_promo",
            "icon": "history",
            "note": "История активаций промокодов пользователями.",
            "sort": 3,
            "translations": [{"language": "ru-RU", "translation": "Использование промокодов"}],
        },
        "connections": {
            "group": "grp_analytics",
            "icon": "timeline",
            "note": "Подключения пользователей",
            "sort": 1,
            "translations": [{"language": "ru-RU", "translation": "Подключения"}],
        },
        "notification_marks": {
            "group": "grp_analytics",
            "icon": "notifications",
            "note": "Служебные отметки рассылок",
            "sort": 2,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Отметки уведомлений"}],
        },
        "processed_payments": {
            "group": "grp_payments",
            "icon": "payments",
            "note": "Обработанные платежи",
            "sort": 1,
            "translations": [{"language": "ru-RU", "translation": "Платежи"}],
        },
        "partner_withdrawals": {
            "group": "grp_partners",
            "icon": "payments",
            "note": "Заявки на вывод средств партнеров",
            "sort": 1,
            "translations": [{"language": "ru-RU", "translation": "Заявки на вывод"}],
        },
        "partner_qr_codes": {
            "group": "grp_partners",
            "icon": "qr_code",
            "note": "QR-коды партнеров и статистика переходов/активаций",
            "sort": 2,
            "translations": [{"language": "ru-RU", "translation": "QR-коды партнеров"}],
        },
        "partner_earnings": {
            "group": "grp_partners",
            "icon": "bar_chart",
            "note": "История начислений по партнерской программе (для графиков и разборов)",
            "sort": 3,
            "translations": [{"language": "ru-RU", "translation": "Начисления партнеров"}],
        },
        "personal_discounts": {
            "group": "grp_service",
            "icon": "percent",
            "note": "Персональные скидки (служебное)",
            "sort": 1,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Персональные скидки"}],
        },
        "hwid_devices_local": {
            "group": "grp_service",
            "icon": "devices",
            "note": "Локальные HWID устройства",
            "sort": 2,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "HWID устройства"}],
        },
        "tvcode": {
            "group": "grp_service",
            "icon": "vpn_key",
            "note": "Служебные коды доступа",
            "sort": 3,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "TV-коды"}],
        },
        "admin": {
            "group": "grp_service",
            "icon": "admin_panel_settings",
            "note": "Админы FastAdmin (исторические)",
            "sort": 4,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Админы FastAdmin"}],
        },
        "aerich": {
            "group": "grp_service",
            "icon": "build",
            "note": "Таблица миграций Aerich",
            "sort": 5,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Миграции Aerich"}],
        },
    }

    for collection, meta in collection_meta_updates.items():
        resp = session.patch(f"{base_url}/collections/{collection}", headers=headers, json={"meta": meta}, timeout=15)
        resp.raise_for_status()

    field_notes = {
        "users": {
            "lte_gb_total": "Лимит LTE в ГБ. Изменение синхронизируется с RemnaWave.",
            "expired_at": "Дата окончания подписки. Изменение синхронизируется с RemnaWave.",
            "hwid_limit": "Лимит устройств (HWID). Изменение синхронизируется с RemnaWave.",
            "balance": "Баланс пользователя в системе.",
            "is_blocked": "Блокировка пользователя.",
        },
        "active_tariffs": {
            "lte_gb_total": "Общий LTE лимит для тарифа.",
            "lte_gb_used": "Использовано LTE (ГБ).",
        },
        "tariffs": {
            "is_active": "Показывать срок в витрине конструктора. Обычно active для 1/3/6/12 месяцев.",
            "order": "Порядок сортировки карточек срока в приложении и Tariff Studio.",
            "name": "Служебное имя тарифа. На витрине основным текстом является срок.",
            "months": "Срок подписки в месяцах. Для v1 конструктора используем 1/3/6/12.",
            "base_price": "Цена за 1 устройство. В Tariff Studio это операторское поле; backend hook также сохраняет его как derived field.",
            "progressive_multiplier": "Advanced pricing internals: скидочная кривая для 2+ устройств. Обычно не редактировать вручную — рассчитывается backend.",
            "devices_limit_default": "Для нового конструктора всегда 1: одно устройство = персональная подписка.",
            "devices_limit_family": "Максимум устройств, доступный в конструкторе для этого срока. Семейная подписка включается с 2 устройств.",
            "family_plan_enabled": "Deprecated product flag. Отдельной family-card больше нет; family определяется device_count >= 2.",
            "final_price_default": "Legacy/derived поле для совместимости. В новой модели равно цене за 1 устройство.",
            "final_price_family": "Legacy anchor/reference для совместимости. Не является отдельным продуктом.",
            "lte_enabled": "Разрешить LTE-добавку для выбранного срока.",
            "lte_price_per_gb": "Цена LTE за 1 ГБ. Используется backend quote и оплатой.",
            "lte_min_gb": "Минимальный LTE объём для selector. 0 = LTE можно не добавлять.",
            "lte_max_gb": "Максимальный LTE объём для selector.",
            "lte_step_gb": "Шаг LTE selector в ГБ.",
            "storefront_badge": "Короткий бейдж на карточке срока: например «выгодно».",
            "storefront_hint": "Короткая подсказка на витрине/в Tariff Studio.",
        },
        "promo_batches": {
            "title": "Название партии/кампании. Отображается в списках.",
            "notes": "Заметки по партии: цель, канал распространения и т.п.",
        },
        "promo_codes": {
            "name": "Человекочитаемое имя для админки (не сам код).",
            "code_hmac": "Введите сырой промокод — хук преобразует в HMAC. Или вставьте готовый 64-символьный hex.",
            "effects": "JSON с эффектами: extend_days, discount_percent, add_hwid и др.",
            "batch_id": "Партия для группировки. Опционально.",
            "max_activations": "Максимум активаций всего. 0 = без лимита.",
            "per_user_limit": "Сколько раз один пользователь может активировать.",
            "expires_at": "Дата истечения (включительно). Пусто = бессрочно.",
            "disabled": "Принудительное отключение. Отключённые коды не принимаются.",
        },
        "promo_usages": {
            "promo_code_id": "Какой промокод был применён.",
            "user_id": "Кто применил промокод.",
            "used_at": "Когда промокод был активирован.",
            "context": "Доп. контекст (payment_id и т.п.).",
        },
    }

    field_translations = {
        "users": {
            "username": "Логин",
            "full_name": "ФИО",
            "expired_at": "Дата окончания",
            "hwid_limit": "Лимит устройств",
            "lte_gb_total": "Лимит LTE (ГБ)",
            "balance": "Баланс",
            "is_blocked": "Заблокирован",
        },
        "active_tariffs": {
            "months": "Месяцев",
            "price": "Цена",
            "lte_gb_total": "Лимит LTE (ГБ)",
            "lte_gb_used": "Использовано LTE (ГБ)",
        },
        "tariffs": {
            "is_active": "Активен",
            "order": "Порядок",
            "name": "Название",
            "months": "Срок, мес.",
            "base_price": "Цена за 1 устройство",
            "progressive_multiplier": "Скидочная кривая",
            "devices_limit_default": "Мин. устройств",
            "devices_limit_family": "Макс. устройств",
            "family_plan_enabled": "Legacy family flag",
            "final_price_default": "Legacy цена 1 устройства",
            "final_price_family": "Legacy family anchor",
            "lte_enabled": "LTE включён",
            "lte_price_per_gb": "LTE ₽/ГБ",
            "lte_min_gb": "Мин. LTE, ГБ",
            "lte_max_gb": "Макс. LTE, ГБ",
            "lte_step_gb": "Шаг LTE, ГБ",
            "storefront_badge": "Бейдж",
            "storefront_hint": "Подсказка",
        },
        "promo_batches": {
            "title": "Название",
            "notes": "Заметки",
            "created_at": "Создана",
        },
        "promo_codes": {
            "name": "Имя",
            "code_hmac": "Код (HMAC)",
            "effects": "Эффекты",
            "batch_id": "Партия",
            "max_activations": "Макс. активаций",
            "per_user_limit": "На пользователя",
            "expires_at": "Истекает",
            "disabled": "Отключен",
        },
        "promo_usages": {
            "promo_code_id": "Промокод",
            "user_id": "Пользователь",
            "used_at": "Дата использования",
            "context": "Контекст",
        },
        "connections": {
            "at": "Дата подключения",
        },
    }

    for collection, fields in field_notes.items():
        for field, note in fields.items():
            meta_payload = {"note": note}
            translation = field_translations.get(collection, {}).get(field)
            if translation:
                meta_payload["translations"] = [{"language": "ru-RU", "translation": translation}]
            resp = session.patch(
                f"{base_url}/fields/{collection}/{field}",
                headers=headers,
                json={"meta": meta_payload},
                timeout=15,
            )
            resp.raise_for_status()

    print("UI/UX navigation and notes updated.")


if __name__ == "__main__":
    main()

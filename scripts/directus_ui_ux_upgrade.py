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
        {"collection": "grp_prizes", "label": "Колесо призов", "icon": "casino", "sort": 3},
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
            "note": "Справочник тарифов",
            "sort": 3,
            "translations": [{"language": "ru-RU", "translation": "Тарифы"}],
        },
        "promo_batches": {
            "group": "grp_promo",
            "icon": "inventory_2",
            "note": "Партии промокодов",
            "sort": 1,
            "translations": [{"language": "ru-RU", "translation": "Партии промокодов"}],
        },
        "promo_codes": {
            "group": "grp_promo",
            "icon": "confirmation_number",
            "note": "Промокоды и эффекты",
            "sort": 2,
            "translations": [{"language": "ru-RU", "translation": "Промокоды"}],
        },
        "promo_usages": {
            "group": "grp_promo",
            "icon": "history",
            "note": "История использования промокодов",
            "sort": 3,
            "translations": [{"language": "ru-RU", "translation": "Использование промокодов"}],
        },
        "prize_wheel_config": {
            "group": "grp_prizes",
            "icon": "casino",
            "note": "Настройки призов и вероятностей",
            "sort": 1,
            "translations": [{"language": "ru-RU", "translation": "Настройки призов"}],
        },
        "prize_wheel_history": {
            "group": "grp_prizes",
            "icon": "history_toggle_off",
            "note": "История выпадения призов",
            "sort": 2,
            "translations": [{"language": "ru-RU", "translation": "История призов"}],
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
        "promo_codes": {
            "code_hmac": "Можно указать сырой код — хук преобразует в HMAC.",
            "effects": "JSON с эффектами промокода.",
        },
        "prize_wheel_config": {
            "probability": "Вероятность от 0 до 1. Сумма активных призов ≤ 1.",
            "prize_value": "Для subscription указывать число дней.",
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
        "promo_codes": {
            "code_hmac": "Код (HMAC)",
            "effects": "Эффекты",
            "disabled": "Отключен",
            "batch_id": "Партия",
        },
        "prize_wheel_config": {
            "prize_type": "Тип приза",
            "prize_name": "Название",
            "prize_value": "Значение",
            "probability": "Вероятность",
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

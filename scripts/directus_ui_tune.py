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

    updates = {
        "users": {
            "group": "Основное",
            "icon": "people",
            "note": "Пользователи и их параметры подписки",
            "sort": 1,
            "display_template": "{{username}} — {{full_name}}",
        },
        "active_tariffs": {
            "group": "Основное",
            "icon": "subscriptions",
            "note": "Текущие активные тарифы пользователей",
            "sort": 2,
        },
        "tariffs": {
            "group": "Основное",
            "icon": "sell",
            "note": "Справочник тарифов и цен",
            "sort": 3,
        },
        "promo_batches": {
            "group": "Промо",
            "icon": "inventory_2",
            "note": "Партии промокодов",
            "sort": 1,
        },
        "promo_codes": {
            "group": "Промо",
            "icon": "confirmation_number",
            "note": "Промокоды и их эффекты",
            "sort": 2,
        },
        "promo_usages": {
            "group": "Промо",
            "icon": "history",
            "note": "История использования промокодов",
            "sort": 3,
        },
        "processed_payments": {
            "group": "Платежи",
            "icon": "payments",
            "note": "Обработанные платежи",
            "sort": 1,
        },
        "connections": {
            "group": "Аналитика",
            "icon": "timeline",
            "note": "Подключения пользователей (для графиков)",
            "sort": 1,
        },
        "notification_marks": {
            "group": "Аналитика",
            "icon": "notifications",
            "note": "Служебные отметки рассылок",
            "sort": 2,
        },
        "personal_discounts": {
            "group": "Служебное",
            "icon": "percent",
            "note": "Персональные скидки (служебная таблица)",
            "sort": 1,
        },
        "hwid_devices_local": {
            "group": "Служебное",
            "icon": "devices",
            "note": "Локальные HWID устройства",
            "sort": 2,
        },
        "tvcode": {
            "group": "Служебное",
            "icon": "vpn_key",
            "note": "Служебные коды доступа",
            "sort": 3,
        },
        "admin": {
            "group": "Служебное",
            "icon": "admin_panel_settings",
            "note": "Админы FastAdmin (исторические записи)",
            "sort": 4,
        },
        "aerich": {
            "group": "Служебное",
            "icon": "build",
            "note": "Таблица миграций Aerich",
            "sort": 5,
        },
    }

    for collection, meta in updates.items():
        payload_meta = {key: value for key, value in meta.items() if key != "group"}
        resp = session.patch(
            f"{base_url}/collections/{collection}",
            headers=headers,
            json={"meta": payload_meta},
            timeout=15,
        )
        if resp.ok:
            print(f"{collection}: ok")
            continue
        print(f"{collection}: {resp.status_code} {resp.text}")


if __name__ == "__main__":
    main()

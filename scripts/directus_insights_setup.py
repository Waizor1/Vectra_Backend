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

    dashboards = session.get(f"{base_url}/dashboards", headers=headers, timeout=15)
    dashboards.raise_for_status()
    existing = next((d for d in dashboards.json().get("data", []) if d.get("name") == "Главный дашборд"), None)
    if existing:
        dashboard_id = existing["id"]
    else:
        created = session.post(
            f"{base_url}/dashboards",
            headers=headers,
            json={"name": "Главный дашборд", "icon": "dashboard", "note": "Ключевые метрики проекта"},
            timeout=15,
        )
        created.raise_for_status()
        dashboard_id = created.json()["data"]["id"]

    panels = session.get(f"{base_url}/panels", headers=headers, params={"filter[dashboard][_eq]": dashboard_id}, timeout=15)
    panels.raise_for_status()
    existing_panels = {p["name"]: p for p in panels.json().get("data", [])}

    panel_defs = [
        {
            "name": "Всего пользователей",
            "type": "metrics",
            "icon": "people",
            "position_x": 1,
            "position_y": 1,
            "width": 4,
            "height": 4,
            "options": {"collection": "users", "field": "id", "function": "count"},
        },
        {
            "name": "Активных тарифов",
            "type": "metrics",
            "icon": "subscriptions",
            "position_x": 5,
            "position_y": 1,
            "width": 4,
            "height": 4,
            "options": {"collection": "active_tariffs", "field": "id", "function": "count"},
        },
        {
            "name": "Промокодов использовано",
            "type": "metrics",
            "icon": "confirmation_number",
            "position_x": 9,
            "position_y": 1,
            "width": 4,
            "height": 4,
            "options": {"collection": "promo_usages", "field": "id", "function": "count"},
        },
        {
            "name": "Регистрации пользователей",
            "type": "time-series",
            "icon": "timeline",
            "position_x": 1,
            "position_y": 5,
            "width": 6,
            "height": 8,
            "options": {
                "collection": "users",
                "dateField": "registration_date",
                "valueField": "id",
                "function": "count",
                "precision": "day",
                "range": "90 days",
                "color": "#3B82F6",
            },
        },
        {
            "name": "Подключения",
            "type": "time-series",
            "icon": "wifi",
            "position_x": 7,
            "position_y": 5,
            "width": 6,
            "height": 8,
            "options": {
                "collection": "connections",
                "dateField": "at",
                "valueField": "id",
                "function": "count",
                "precision": "day",
                "range": "90 days",
                "color": "#10B981",
            },
        },
    ]

    for panel in panel_defs:
        if panel["name"] in existing_panels:
            continue
        payload = {
            "dashboard": dashboard_id,
            "name": panel["name"],
            "icon": panel["icon"],
            "show_header": True,
            "type": panel["type"],
            "position_x": panel["position_x"],
            "position_y": panel["position_y"],
            "width": panel["width"],
            "height": panel["height"],
            "options": panel["options"],
        }
        resp = session.post(f"{base_url}/panels", headers=headers, json=payload, timeout=15)
        resp.raise_for_status()

    print("Insights dashboard configured.")


if __name__ == "__main__":
    main()

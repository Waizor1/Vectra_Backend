from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

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

    for collection in ("users", "connections", "active_tariffs", "promo_usages"):
        resp = session.get(f"{base_url}/fields/{collection}", headers=headers, timeout=15)
        resp.raise_for_status()
        fields = [f["field"] for f in resp.json().get("data", [])]
        print(collection, "fields:", [f for f in fields if f in ("registration_date", "at", "id")])

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=90)).date().isoformat()
    users_count = session.get(
        f"{base_url}/items/users",
        headers=headers,
        params={"filter[registration_date][_gte]": since, "aggregate[count]": "id"},
        timeout=15,
    )
    users_count.raise_for_status()
    print("users last 90 days:", users_count.json().get("data"))

    conn_count = session.get(
        f"{base_url}/items/connections",
        headers=headers,
        params={"filter[at][_gte]": since, "aggregate[count]": "id"},
        timeout=15,
    )
    conn_count.raise_for_status()
    print("connections last 90 days:", conn_count.json().get("data"))

    dashboards = session.get(f"{base_url}/dashboards", headers=headers, timeout=15)
    dashboards.raise_for_status()
    main = next((d for d in dashboards.json().get("data", []) if d.get("name") == "Главный дашборд"), None)
    print("dashboard", main and main.get("id"))
    if not main:
        return

    panels = session.get(
        f"{base_url}/panels",
        headers=headers,
        params={"filter[dashboard][_eq]": main["id"]},
        timeout=15,
    )
    panels.raise_for_status()
    for panel in panels.json().get("data", []):
        print(panel["name"], panel["type"], panel.get("options"))


if __name__ == "__main__":
    main()

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

    roles = session.get(f"{base_url}/roles", headers=headers, timeout=15)
    roles.raise_for_status()
    admin_role = next((r for r in roles.json().get("data", []) if r.get("name") == "Administrator"), None)
    if not admin_role:
        raise SystemExit("Administrator role not found")
    role_id = admin_role["id"]

    permissions = session.get(
        f"{base_url}/permissions", headers=headers, params={"filter[role][_eq]": role_id}, timeout=15
    )
    permissions.raise_for_status()
    existing = permissions.json().get("data", [])
    existing_keys = {(p.get("collection"), p.get("action")) for p in existing}

    collections = [
        "users",
        "tariffs",
        "active_tariffs",
        "promo_batches",
        "promo_codes",
        "promo_usages",
        "prize_wheel_config",
        "prize_wheel_history",
        "connections",
        "processed_payments",
        "notification_marks",
        "personal_discounts",
        "hwid_devices_local",
        "tvcode",
        "admin",
        "aerich",
    ]
    actions = ["read", "create", "update", "delete"]

    created = 0
    for collection in collections:
        for action in actions:
            if (collection, action) in existing_keys:
                continue
            payload = {
                "role": role_id,
                "collection": collection,
                "action": action,
                "fields": ["*"],
                "permissions": {},
                "validation": None,
                "presets": None,
            }
            resp = session.post(f"{base_url}/permissions", headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
            created += 1

    print(f"Permissions ensured. Created: {created}")


if __name__ == "__main__":
    main()

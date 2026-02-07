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
    role_map = {role.get("name"): role for role in roles.json().get("data", [])}

    target_roles = [name for name in ("Manager", "Viewer") if name in role_map]
    if not target_roles:
        print("Roles Manager/Viewer not found. Nothing to do.")
        return

    collections = ["users", "connections", "active_tariffs", "promo_usages"]
    created = 0
    for role_name in target_roles:
        role_id = role_map[role_name]["id"]
        permissions = session.get(
            f"{base_url}/permissions", headers=headers, params={"filter[role][_eq]": role_id}, timeout=15
        )
        permissions.raise_for_status()
        existing = permissions.json().get("data", [])
        existing_keys = {(p.get("collection"), p.get("action")) for p in existing}
        for collection in collections:
            if (collection, "read") in existing_keys:
                continue
            payload = {
                "role": role_id,
                "collection": collection,
                "action": "read",
                "fields": ["*"],
                "permissions": {},
                "validation": None,
                "presets": None,
            }
            resp = session.post(f"{base_url}/permissions", headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
            created += 1
        print(f"{role_name}: ensured read on {len(collections)} collections.")

    print(f"Permissions created: {created}")


if __name__ == "__main__":
    main()

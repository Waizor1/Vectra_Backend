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


def ensure_permission(session: requests.Session, base_url: str, headers: dict[str, str], *, policy_id: str, collection: str, action: str) -> bool:
    payload = {
        "policy": policy_id,
        "collection": collection,
        "action": action,
        "fields": ["*"],
        "permissions": {},
        "validation": None,
        "presets": None,
    }
    resp = session.post(f"{base_url}/permissions", headers=headers, json=payload, timeout=15)
    if resp.ok:
        return True
    if resp.status_code == 409:
        return False
    try:
        errors = resp.json().get("errors") or []
        msg = " ".join(str(e.get("message") or "") for e in errors).lower()
    except Exception:  # pragma: no cover
        msg = ""
    if "unique" in msg or "already exists" in msg:
        return False
    resp.raise_for_status()
    return False


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

    policies = session.get(f"{base_url}/policies", headers=headers, params={"fields": "id,name", "limit": 200}, timeout=15)
    policies.raise_for_status()
    policy_map = {p.get("name"): p for p in policies.json().get("data", []) if p.get("name")}

    target_policies = [name for name in ("Manager", "Viewer") if name in policy_map]
    if not target_policies:
        print("Policies Manager/Viewer not found. Nothing to do.")
        return

    collections = ["users", "connections", "active_tariffs", "promo_usages"]
    created = 0
    for policy_name in target_policies:
        policy_id = policy_map[policy_name]["id"]
        for collection in collections:
            if ensure_permission(session, base_url, headers, policy_id=policy_id, collection=collection, action="read"):
                created += 1
        print(f"{policy_name}: ensured read on {len(collections)} collections.")

    print(f"Permissions created: {created}")


if __name__ == "__main__":
    main()

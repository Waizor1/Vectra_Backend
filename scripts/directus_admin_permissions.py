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


def ensure_permission(session: requests.Session, base_url: str, headers: dict[str, str], *, policy_id: str, collection: str, action: str) -> None:
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
    if resp.ok or resp.status_code == 409:
        return
    try:
        errors = resp.json().get("errors") or []
        msg = " ".join(str(e.get("message") or "") for e in errors).lower()
    except Exception:  # pragma: no cover
        msg = ""
    if "unique" in msg or "already exists" in msg:
        return
    resp.raise_for_status()


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

    # Directus v11 assigns permissions to policies, not roles.
    policies = session.get(f"{base_url}/policies", headers=headers, params={"fields": "id,name", "limit": 200}, timeout=15)
    policies.raise_for_status()
    admin_policy = next((p for p in policies.json().get("data", []) if p.get("name") == "Administrator"), None)
    if not admin_policy:
        raise SystemExit("Administrator policy not found")
    policy_id = admin_policy["id"]

    collections = [
        "users",
        "tariffs",
        "active_tariffs",
        "promo_batches",
        "promo_codes",
        "promo_usages",
        "connections",
        "processed_payments",
        "analytics_payment_events",
        "analytics_service_daily",
        "analytics_trial_daily",
        "analytics_trial_risk_flags",
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
            ensure_permission(session, base_url, headers, policy_id=policy_id, collection=collection, action=action)
            created += 1

    print(f"Permissions ensured. Created: {created}")


if __name__ == "__main__":
    main()

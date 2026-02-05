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

    collections = [
        "users",
        "active_tariffs",
        "tariffs",
        "promo_batches",
        "promo_codes",
        "promo_usages",
        "prize_wheel_config",
        "prize_wheel_history",
        "connections",
        "notification_marks",
        "personal_discounts",
        "hwid_devices_local",
        "tvcode",
        "admin",
        "aerich",
        "processed_payments",
    ]

    reset_meta = {
        "group": None,
        "hidden": False,
        "icon": None,
        "note": None,
        "sort": None,
        "display_template": None,
        "translations": None,
    }

    for collection in collections:
        resp = session.patch(
            f"{base_url}/collections/{collection}",
            headers=headers,
            json={"meta": reset_meta},
            timeout=15,
        )
        resp.raise_for_status()

    field_notes = {
        "users": ["lte_gb_total", "expired_at", "hwid_limit", "balance", "is_blocked", "username", "full_name"],
        "active_tariffs": ["lte_gb_total", "lte_gb_used", "months", "price"],
        "promo_codes": ["code_hmac", "effects", "disabled", "batch_id"],
        "prize_wheel_config": ["probability", "prize_value", "prize_type", "prize_name"],
        "connections": ["at"],
    }

    for collection, fields in field_notes.items():
        for field in fields:
            resp = session.patch(
                f"{base_url}/fields/{collection}/{field}",
                headers=headers,
                json={"meta": {"note": None, "translations": None}},
                timeout=15,
            )
            resp.raise_for_status()

    group_collections = [
        "grp_main",
        "grp_promo",
        "grp_prizes",
        "grp_analytics",
        "grp_payments",
        "grp_service",
    ]

    for group in group_collections:
        resp = session.get(f"{base_url}/collections/{group}", headers=headers, timeout=15)
        if resp.status_code == 200:
            session.delete(f"{base_url}/collections/{group}", headers=headers, timeout=15).raise_for_status()

    print("UI/UX rollback completed.")


if __name__ == "__main__":
    main()

import os
from typing import Dict, Iterable, List, Optional

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def directus_login(base_url: str, email: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"]["access_token"]


def field_exists(base_url: str, token: str, collection: str, field: str) -> bool:
    resp = requests.get(
        f"{base_url}/fields/{collection}/{field}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if resp.status_code in (403, 404):
        return False
    resp.raise_for_status()
    return True


def patch_field_meta(
    base_url: str,
    token: str,
    collection: str,
    field: str,
    meta: Dict[str, object],
) -> None:
    resp = requests.patch(
        f"{base_url}/fields/{collection}/{field}",
        headers={"Authorization": f"Bearer {token}"},
        json={"meta": meta},
        timeout=15,
    )
    resp.raise_for_status()


def set_readonly_fields(
    base_url: str,
    token: str,
    collection: str,
    readonly_fields: Iterable[str],
) -> List[str]:
    updated: List[str] = []
    for field in readonly_fields:
        if not field_exists(base_url, token, collection, field):
            continue
        patch_field_meta(base_url, token, collection, field, {"readonly": True})
        updated.append(field)
    return updated


def main() -> None:
    if load_dotenv:
        load_dotenv()
    base_url = env("DIRECTUS_URL")
    email = env("DIRECTUS_ADMIN_EMAIL")
    password = env("DIRECTUS_ADMIN_PASSWORD")
    if not base_url or not email or not password:
        raise SystemExit("DIRECTUS_URL / DIRECTUS_ADMIN_EMAIL / DIRECTUS_ADMIN_PASSWORD not set")

    token = directus_login(base_url, email, password)

    readonly_map = {
        "users": [
            "id",
            "registration_date",
            "activation_date",
            "referrals",
            "username",
            "full_name",
        ],
        "active_tariffs": [
            "id",
            "user",
            "name",
            "months",
            "price",
            "hwid_limit",
            "lte_gb_used",
            "lte_price_per_gb",
            "lte_autopay_free",
            "lte_usage_last_date",
            "lte_usage_last_total_gb",
            "progressive_multiplier",
            "residual_day_fraction",
            "devices_decrease_count",
        ],
        "promo_batches": ["id", "created_at"],
        "promo_codes": ["id", "created_at"],
        "promo_usages": ["id", "used_at"],
        "connections": ["id", "user_id", "at"],
        "notification_marks": ["id", "sent_at"],
        "personal_discounts": ["id", "created_at"],
        "hwid_devices_local": ["id", "first_seen_at", "last_seen_at"],
        "processed_payments": ["id", "processed_at"],
    }

    for collection, fields in readonly_map.items():
        updated = set_readonly_fields(base_url, token, collection, fields)
        if updated:
            print(f"{collection}: readonly -> {', '.join(updated)}")
        else:
            print(f"{collection}: no readonly fields updated (collection may be missing)")


if __name__ == "__main__":
    main()

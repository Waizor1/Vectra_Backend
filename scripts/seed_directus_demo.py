from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


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

    now = datetime.now(timezone.utc)

    def find_one(collection: str, field: str, value) -> int | None:
        resp = session.get(
            f"{base_url}/items/{collection}",
            headers=headers,
            params={f"filter[{field}][_eq]": value, "limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            return None
        return data[0]["id"]

    def count_items(collection: str) -> int:
        resp = session.get(
            f"{base_url}/items/{collection}",
            headers=headers,
            params={"aggregate[count]": "id"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            return 0
        return int(data[0]["count"]["id"])

    tariffs_payload = [
        {
            "name": "Base",
            "months": 1,
            "base_price": 500,
            "order": 1,
            "lte_enabled": True,
            "lte_price_per_gb": 50,
            "progressive_multiplier": 1.0,
            "lte_usage_last_total_gb": 0,
            "lte_autopay_free": False,
        },
        {
            "name": "Pro",
            "months": 3,
            "base_price": 1200,
            "order": 2,
            "lte_enabled": True,
            "lte_price_per_gb": 40,
            "progressive_multiplier": 1.2,
            "lte_usage_last_total_gb": 0,
            "lte_autopay_free": True,
        },
    ]
    tariff_ids: list[int] = []
    for payload in tariffs_payload:
        existing = find_one("tariffs", "name", payload["name"])
        if existing:
            tariff_ids.append(existing)
            continue
        resp = session.post(f"{base_url}/items/tariffs", headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        tariff_ids.append(resp.json()["data"]["id"])

    users_payload = []
    for idx in range(1, 6):
        users_payload.append(
            {
                "username": f"user{idx}",
                "full_name": f"Test User {idx}",
                "is_registered": True,
                "balance": 1000 * idx,
                "referred_by": 0,
                "is_admin": False,
                "custom_referral_percent": 0,
                "referrals": 0,
                "is_subscribed": True,
                "is_partner": False,
                "is_blocked": False,
                "failed_message_count": 0,
                "prize_wheel_attempts": 3,
                "hwid_limit": 2,
                "lte_gb_total": 10,
                "expired_at": (now + timedelta(days=30 * idx)).date().isoformat(),
                "registration_date": iso(now - timedelta(days=idx * 7)),
            }
        )

    user_ids: list[int] = []
    for payload in users_payload:
        existing = find_one("users", "username", payload["username"])
        if existing:
            user_ids.append(existing)
            continue
        resp = session.post(f"{base_url}/items/users", headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        user_ids.append(resp.json()["data"]["id"])

    active_tariffs_payload = [
        {
            "id": "AT001",
            "user_id": user_ids[0],
            "name": tariffs_payload[0]["name"],
            "months": tariffs_payload[0]["months"],
            "price": tariffs_payload[0]["base_price"],
            "lte_gb_total": 10,
            "lte_price_per_gb": tariffs_payload[0]["lte_price_per_gb"],
        },
        {
            "id": "AT002",
            "user_id": user_ids[1],
            "name": tariffs_payload[1]["name"],
            "months": tariffs_payload[1]["months"],
            "price": tariffs_payload[1]["base_price"],
            "lte_gb_total": 15,
            "lte_price_per_gb": tariffs_payload[1]["lte_price_per_gb"],
        },
    ]
    for payload in active_tariffs_payload:
        existing = find_one("active_tariffs", "id", payload["id"])
        if existing:
            continue
        resp = session.post(f"{base_url}/items/active_tariffs", headers=headers, json=payload, timeout=15)
        resp.raise_for_status()

    existing_batch = find_one("promo_batches", "title", "Test Batch")
    if existing_batch:
        promo_batch_id = existing_batch
    else:
        promo_batch = session.post(
            f"{base_url}/items/promo_batches",
            headers=headers,
            json={"title": "Test Batch"},
            timeout=15,
        )
        promo_batch.raise_for_status()
        promo_batch_id = promo_batch.json()["data"]["id"]

    promo_codes_payload = [
        {"code_hmac": "TESTCODE1", "effects": {"bonus": 10}, "disabled": False, "batch_id": promo_batch_id},
        {"code_hmac": "TESTCODE2", "effects": {"bonus": 20}, "disabled": False, "batch_id": promo_batch_id},
    ]
    promo_code_ids: list[int] = []
    for payload in promo_codes_payload:
        existing = find_one("promo_codes", "code_hmac", payload["code_hmac"])
        if existing:
            promo_code_ids.append(existing)
            continue
        resp = session.post(f"{base_url}/items/promo_codes", headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        promo_code_ids.append(resp.json()["data"]["id"])

    if count_items("promo_usages") == 0:
        promo_usage = {
            "promo_code_id": promo_code_ids[0],
            "user_id": user_ids[0],
            "context": {"source": "seed"},
        }
        session.post(f"{base_url}/items/promo_usages", headers=headers, json=promo_usage, timeout=15).raise_for_status()

    prize_configs = [
        {"prize_type": "subscription", "prize_name": "30 дней", "prize_value": "30", "probability": 0.2, "requires_admin": False},
        {"prize_type": "balance", "prize_name": "100 ₽", "prize_value": "100", "probability": 0.3, "requires_admin": False},
        {"prize_type": "discount", "prize_name": "Скидка 10%", "prize_value": "10", "probability": 0.1, "requires_admin": False},
    ]
    if count_items("prize_wheel_config") == 0:
        for payload in prize_configs:
            session.post(f"{base_url}/items/prize_wheel_config", headers=headers, json=payload, timeout=15).raise_for_status()

    prize_history = [
        {
            "user_id": user_ids[0],
            "prize_type": "subscription",
            "prize_name": "30 дней",
            "prize_value": "30",
            "is_claimed": True,
            "admin_notified": False,
            "is_rejected": False,
        },
        {
            "user_id": user_ids[1],
            "prize_type": "balance",
            "prize_name": "100 ₽",
            "prize_value": "100",
            "is_claimed": False,
            "admin_notified": True,
            "is_rejected": False,
        },
    ]
    if count_items("prize_wheel_history") == 0:
        for payload in prize_history:
            session.post(f"{base_url}/items/prize_wheel_history", headers=headers, json=payload, timeout=15).raise_for_status()

    if count_items("connections") == 0:
        connections_payload = []
        for day in range(10):
            for _ in range(3):
                connections_payload.append(
                    {
                        "user_id": random.choice(user_ids),
                        "at": iso(now - timedelta(days=day, hours=random.randint(0, 23))),
                    }
                )
        for payload in connections_payload:
            session.post(f"{base_url}/items/connections", headers=headers, json=payload, timeout=15).raise_for_status()

    print("Seed completed.")


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import random
from datetime import date, datetime, timedelta, timezone

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


def chunks(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> None:
    if load_dotenv:
        load_dotenv()

    base_url = env("DIRECTUS_URL")
    email = env("DIRECTUS_ADMIN_EMAIL")
    password = env("DIRECTUS_ADMIN_PASSWORD")

    months_back = int(os.getenv("DEMO_MONTHS_BACK", "6"))
    users_count = int(os.getenv("DEMO_USERS_COUNT", "160"))
    daily_min = int(os.getenv("DEMO_CONNECTIONS_MIN", "10"))
    daily_max = int(os.getenv("DEMO_CONNECTIONS_MAX", "40"))

    session = requests.Session()
    login = session.post(f"{base_url}/auth/login", json={"email": email, "password": password}, timeout=15)
    login.raise_for_status()
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=months_back * 30)

    existing_demo = set()
    page = 1
    while True:
        resp = session.get(
            f"{base_url}/items/users",
            headers=headers,
            params={
                "filter[username][_starts_with]": "demo_user_",
                "fields": "id,username",
                "limit": 200,
                "page": page,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            break
        for item in data:
            existing_demo.add(item["username"])
        page += 1

    max_suffix = 0
    for username in existing_demo:
        try:
            max_suffix = max(max_suffix, int(username.split("_")[-1]))
        except ValueError:
            continue

    new_users: list[dict] = []
    for idx in range(1, users_count + 1):
        suffix = max_suffix + idx
        username = f"demo_user_{suffix}"
        if username in existing_demo:
            continue
        reg_offset = random.randint(0, (today - start_date).days)
        reg_date = datetime.combine(start_date + timedelta(days=reg_offset), datetime.min.time(), tzinfo=timezone.utc)
        new_users.append(
            {
                "username": username,
                "full_name": f"Demo User {suffix}",
                "email": f"demo{suffix}@example.com",
                "is_registered": True,
                "balance": random.randint(0, 5000),
                "referred_by": 0,
                "is_admin": False,
                "custom_referral_percent": 0,
                "referrals": random.randint(0, 5),
                "is_subscribed": True,
                "is_partner": False,
                "is_blocked": False,
                "failed_message_count": 0,
                "prize_wheel_attempts": random.randint(0, 5),
                "hwid_limit": random.randint(1, 3),
                "lte_gb_total": random.choice([5, 10, 15, 20]),
                "expired_at": (reg_date.date() + timedelta(days=random.choice([30, 60, 90]))).isoformat(),
                "registration_date": iso(reg_date),
            }
        )

    if new_users:
        for payload in chunks(new_users, 50):
            resp = session.post(f"{base_url}/items/users", headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
        print(f"Created users: {len(new_users)}")
    else:
        print("No new users created.")

    user_ids: list[int] = []
    page = 1
    while True:
        resp = session.get(
            f"{base_url}/items/users",
            headers=headers,
            params={"fields": "id", "limit": 500, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            break
        user_ids.extend([int(item["id"]) for item in data])
        page += 1

    tariffs = session.get(
        f"{base_url}/items/tariffs",
        headers=headers,
        params={"fields": "id,name,months,base_price,lte_price_per_gb", "limit": 50},
        timeout=15,
    )
    tariffs.raise_for_status()
    tariff_items = tariffs.json().get("data") or []

    active_ids = set()
    page = 1
    while True:
        resp = session.get(
            f"{base_url}/items/active_tariffs",
            headers=headers,
            params={"fields": "id", "limit": 500, "page": page},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        if not data:
            break
        active_ids.update(item["id"] for item in data)
        page += 1

    active_payloads: list[dict] = []
    if tariff_items and user_ids:
        sample_users = random.sample(user_ids, min(len(user_ids), max(20, users_count // 2)))
        for user_id in sample_users:
            tariff = random.choice(tariff_items)
            while True:
                active_id = f"A{random.randint(1000, 9999)}"
                if active_id not in active_ids:
                    active_ids.add(active_id)
                    break
            active_payloads.append(
                {
                    "id": active_id,
                    "user_id": user_id,
                    "name": tariff["name"],
                    "months": tariff["months"],
                    "price": tariff["base_price"],
                    "lte_gb_total": random.choice([5, 10, 15, 20]),
                    "lte_price_per_gb": tariff["lte_price_per_gb"],
                }
            )

    if active_payloads:
        for payload in chunks(active_payloads, 50):
            session.post(f"{base_url}/items/active_tariffs", headers=headers, json=payload, timeout=30).raise_for_status()
        print(f"Created active tariffs: {len(active_payloads)}")

    if not user_ids:
        print("No users found for connections.")
        return

    connections_payloads: list[dict] = []
    days_total = (today - start_date).days
    for offset in range(days_total + 1):
        day = start_date + timedelta(days=offset)
        count = random.randint(daily_min, daily_max)
        for _ in range(count):
            connections_payloads.append(
                {
                    "user_id": random.choice(user_ids),
                    "at": day.isoformat(),
                }
            )

    if connections_payloads:
        for payload in chunks(connections_payloads, 200):
            session.post(f"{base_url}/items/connections", headers=headers, json=payload, timeout=30).raise_for_status()
        print(f"Created connections: {len(connections_payloads)}")


if __name__ == "__main__":
    main()

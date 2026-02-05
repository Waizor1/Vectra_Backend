import asyncio
import os
from typing import Optional

import requests
from tortoise import Tortoise

from bloobcat.db.admins import Admin
from bloobcat.settings import script_settings

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None or value.strip() == "":
        return None
    return value


def directus_login(base_url: str, email: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["data"]["access_token"]


def directus_get_admin_role_id(base_url: str, token: str) -> Optional[str]:
    resp = requests.get(
        f"{base_url}/roles",
        params={"filter[admin_access][_eq]": "true", "limit": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    if not data:
        return None
    return data[0]["id"]


def directus_user_exists(base_url: str, token: str, email: str) -> bool:
    resp = requests.get(
        f"{base_url}/users",
        params={"filter[email][_eq]": email, "limit": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    return len(data) > 0


def directus_create_user(base_url: str, token: str, email: str, password: str, role_id: str, username: str) -> None:
    payload = {
        "email": email,
        "password": password,
        "role": role_id,
        "status": "active",
        "first_name": username,
    }
    resp = requests.post(
        f"{base_url}/users",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()


async def main() -> None:
    if load_dotenv:
        load_dotenv()
    directus_url = env("DIRECTUS_URL")
    directus_admin_email = env("DIRECTUS_ADMIN_EMAIL")
    directus_admin_password = env("DIRECTUS_ADMIN_PASSWORD")
    import_password = env("DIRECTUS_IMPORTED_ADMIN_PASSWORD") or directus_admin_password

    if not directus_url or not directus_admin_email or not directus_admin_password:
        raise SystemExit("DIRECTUS_URL / DIRECTUS_ADMIN_EMAIL / DIRECTUS_ADMIN_PASSWORD not set")
    if not import_password:
        raise SystemExit("DIRECTUS_IMPORTED_ADMIN_PASSWORD not set")

    minimal_orm = {
        "connections": {"default": script_settings.db.get_secret_value()},
        "apps": {
            "models": {
                "models": ["bloobcat.db.admins"],
                "default_connection": "default",
            }
        },
    }
    await Tortoise.init(config=minimal_orm)
    try:
        admins = await Admin.all()
    finally:
        await Tortoise.close_connections()

    if not admins:
        print("No Admin records found. Nothing to migrate.")
        return

    token = directus_login(directus_url, directus_admin_email, directus_admin_password)
    role_id = directus_get_admin_role_id(directus_url, token)
    if not role_id:
        raise SystemExit("Directus admin role not found (admin_access=true)")

    created = 0
    skipped = 0
    for admin in admins:
        email = f"{admin.username}@admin.local"
        if directus_user_exists(directus_url, token, email):
            skipped += 1
            continue
        directus_create_user(
            directus_url,
            token,
            email=email,
            password=import_password,
            role_id=role_id,
            username=admin.username,
        )
        created += 1

    print(f"Directus admin users created: {created}, skipped: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())

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

    settings_payload = {"default_language": "ru-RU"}
    settings = session.patch(f"{base_url}/settings", headers=headers, json=settings_payload, timeout=15)
    settings.raise_for_status()

    user_payload = {"language": "ru-RU"}
    user = session.patch(f"{base_url}/users/me", headers=headers, json=user_payload, timeout=15)
    user.raise_for_status()

    print("Language set to ru-RU.")


if __name__ == "__main__":
    main()

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


TOKEN_MAP = {
    "id": "ID",
    "user": "Пользователь",
    "users": "Пользователи",
    "username": "Логин",
    "full": "Полное",
    "name": "имя",
    "email": "Email",
    "balance": "Баланс",
    "expired": "Окончание",
    "at": "Дата",
    "date": "Дата",
    "created": "Создан",
    "updated": "Обновлен",
    "registration": "Регистрация",
    "active": "Активный",
    "tariff": "Тариф",
    "tariffs": "Тарифы",
    "promo": "Промо",
    "code": "Код",
    "codes": "Коды",
    "batch": "Партия",
    "batches": "Партии",
    "usage": "Использование",
    "usages": "Использования",
    "prize": "Приз",
    "wheel": "Колесо",
    "config": "Настройки",
    "history": "История",
    "connection": "Подключение",
    "connections": "Подключения",
    "payments": "Платежи",
    "processed": "Обработанные",
    "notification": "Уведомление",
    "notifications": "Уведомления",
    "mark": "Отметка",
    "marks": "Отметки",
    "discount": "Скидка",
    "discounts": "Скидки",
    "hwid": "HWID",
    "device": "Устройство",
    "devices": "Устройства",
    "lte": "LTE",
    "gb": "ГБ",
    "total": "Лимит",
    "used": "Использовано",
    "price": "Цена",
    "per": "За",
    "month": "Месяц",
    "months": "Месяцев",
    "value": "Значение",
    "probability": "Вероятность",
    "type": "Тип",
    "is": "",
    "admin": "Админ",
    "blocked": "Заблокирован",
    "partner": "Партнер",
    "registered": "Зарегистрирован",
    "subscribed": "Подписан",
    "trial": "Тест",
    "failed": "Ошибки",
    "count": "Количество",
    "attempts": "Попытки",
    "limit": "Лимит",
    "progressive": "Прогрессивный",
    "multiplier": "Множитель",
    "residual": "Остаток",
    "day": "День",
    "fraction": "Доля",
    "autopay": "Автоплатеж",
    "free": "Бесплатный",
    "reset": "Сброс",
    "language": "Язык",
    "code": "Код",
    "utm": "UTM",
    "referred": "Реферал",
    "referrals": "Рефералы",
    "custom": "Кастомный",
    "percent": "Процент",
}


def translate_field(field: str) -> str:
    tokens = field.split("_")
    parts: list[str] = []
    for token in tokens:
        if token in ("", "is"):
            continue
        if token.lower() in TOKEN_MAP:
            parts.append(TOKEN_MAP[token.lower()])
        else:
            parts.append(token.upper())
    return " ".join([p for p in parts if p]).strip() or field


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

    for collection in collections:
        fields_resp = session.get(f"{base_url}/fields/{collection}", headers=headers, timeout=15)
        fields_resp.raise_for_status()
        fields = fields_resp.json().get("data", [])
        for field in fields:
            field_name = field["field"]
            translation = translate_field(field_name)
            meta_payload = {
                "translations": [{"language": "ru-RU", "translation": translation}],
            }
            resp = session.patch(
                f"{base_url}/fields/{collection}/{field_name}",
                headers=headers,
                json={"meta": meta_payload},
                timeout=15,
            )
            resp.raise_for_status()

    print("Field translations updated.")


if __name__ == "__main__":
    main()

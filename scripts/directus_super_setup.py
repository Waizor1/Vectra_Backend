from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def env(name: str, default: Optional[str] = None, *, required: bool = True) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None:
        if required:
            raise SystemExit(f"{name} not set")
        return None
    value = value.strip()
    if not value:
        if required:
            raise SystemExit(f"{name} not set")
        return None
    return value


@dataclass(frozen=True)
class DirectusAuth:
    base_url: str
    token: str

    @property
    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class DirectusClient:
    def __init__(self, auth: DirectusAuth, *, timeout: int = 20) -> None:
        self.auth = auth
        self.session = requests.Session()
        self.timeout = timeout

    def get(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        return self.session.get(
            f"{self.auth.base_url}{path}",
            headers=self.auth.headers,
            params=params,
            timeout=self.timeout,
        )

    def post(self, path: str, *, json: Optional[Dict[str, Any]] = None) -> requests.Response:
        return self.session.post(
            f"{self.auth.base_url}{path}",
            headers=self.auth.headers,
            json=json,
            timeout=self.timeout,
        )

    def patch(self, path: str, *, json: Optional[Dict[str, Any]] = None) -> requests.Response:
        return self.session.patch(
            f"{self.auth.base_url}{path}",
            headers=self.auth.headers,
            json=json,
            timeout=self.timeout,
        )


def login(base_url: str, email: str, password: str) -> DirectusAuth:
    resp = requests.post(
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
        timeout=20,
    )
    resp.raise_for_status()
    token = resp.json()["data"]["access_token"]
    return DirectusAuth(base_url=base_url.rstrip("/"), token=token)


def list_collections(client: DirectusClient) -> list[str]:
    # We only need collection keys; keep it simple.
    resp = client.get("/collections", params={"limit": 500})
    resp.raise_for_status()
    items = resp.json().get("data", [])
    keys = []
    for item in items:
        key = item.get("collection")
        if not key or not isinstance(key, str):
            continue
        if key.startswith("directus_"):
            continue
        keys.append(key)
    return sorted(set(keys))


def get_role_map(client: DirectusClient) -> dict[str, Dict[str, Any]]:
    resp = client.get("/roles", params={"limit": 200})
    resp.raise_for_status()
    roles = resp.json().get("data", [])
    return {r.get("name"): r for r in roles if r.get("name")}


def ensure_role(
    client: DirectusClient,
    name: str,
    *,
    icon: str,
    description: str,
) -> str:
    role_map = get_role_map(client)
    existing = role_map.get(name)
    if existing:
        role_id = existing["id"]
        patch = {
            "icon": icon,
            "description": description,
        }
        client.patch(f"/roles/{role_id}", json=patch).raise_for_status()
        return str(role_id)

    created = client.post(
        "/roles",
        json={
            "name": name,
            "icon": icon,
            "description": description,
        },
    )
    created.raise_for_status()
    return str(created.json()["data"]["id"])


def get_primary_policy_id_for_role(client: DirectusClient, role_id: str) -> Optional[str]:
    # In Directus v11, role<->policy mapping goes through /access.
    # Role endpoints may return access IDs, not policy IDs, depending on fields/permissions.
    resp = client.get(
        "/access",
        params={"filter[role][_eq]": role_id, "fields": "id,policy,sort", "limit": 50, "sort": "sort"},
    )
    if resp.status_code == 403:
        return None
    resp.raise_for_status()
    rows = resp.json().get("data") or []
    if not rows:
        return None
    policy = rows[0].get("policy")
    return str(policy) if policy else None


def get_policy_id_by_name(client: DirectusClient, name: str) -> Optional[str]:
    resp = client.get("/policies", params={"filter[name][_eq]": name, "fields": "id,name", "limit": 1})
    if resp.status_code == 403:
        return None
    resp.raise_for_status()
    data = resp.json().get("data") or []
    if not data:
        return None
    return str(data[0]["id"])


def ensure_access_link_role_policy(client: DirectusClient, role_id: str, policy_id: str) -> None:
    # Ensure policy is assigned to role via /access.
    existing = client.get(
        "/access",
        params={
            "filter[role][_eq]": role_id,
            "filter[policy][_eq]": policy_id,
            "fields": "id",
            "limit": 1,
        },
    )
    existing.raise_for_status()
    rows = existing.json().get("data") or []
    if rows:
        return
    created = client.post("/access", json={"role": role_id, "policy": policy_id, "sort": 1})
    created.raise_for_status()


def ensure_policy_for_role(
    client: DirectusClient,
    role_id: str,
    *,
    name: str,
    icon: str,
    description: str,
    admin_access: bool,
    app_access: bool,
) -> str:
    policy_id = get_policy_id_by_name(client, name)
    if policy_id:
        client.patch(
            f"/policies/{policy_id}",
            json={
                "name": name,
                "icon": icon,
                "description": description,
                "admin_access": admin_access,
                "app_access": app_access,
            },
        ).raise_for_status()
        ensure_access_link_role_policy(client, role_id, policy_id)
        return policy_id

    created = client.post(
        "/policies",
        json={
            "name": name,
            "icon": icon,
            "description": description,
            "admin_access": admin_access,
            "app_access": app_access,
        },
    )
    created.raise_for_status()
    policy_id = str(created.json()["data"]["id"])
    ensure_access_link_role_policy(client, role_id, policy_id)
    return policy_id

def list_permissions_for_role(client: DirectusClient, role_id: str) -> set[tuple[str, str]]:
    # Deprecated: some instances restrict access to the "role" field on directus_permissions.
    # Kept for backwards compatibility with older setups, but avoided in main flow.
    resp = client.get("/permissions", params={"filter[role][_eq]": role_id, "limit": 1000})
    if resp.status_code == 403:
        return set()
    resp.raise_for_status()
    perms = resp.json().get("data", [])
    return {(p.get("collection"), p.get("action")) for p in perms if p.get("collection") and p.get("action")}


def ensure_permission(
    client: DirectusClient,
    policy_id: str,
    collection: str,
    action: str,
    *,
    fields: Optional[list[str]] = None,
    permissions: Optional[Dict[str, Any]] = None,
    validation: Any = None,
    presets: Any = None,
) -> bool:
    payload: Dict[str, Any] = {
        "policy": policy_id,
        "collection": collection,
        "action": action,
        "fields": fields or ["*"],
        "permissions": permissions or {},
        "validation": validation,
        "presets": presets,
    }
    resp = client.post("/permissions", json=payload)
    if resp.ok:
        return True

    # Idempotency: Directus can reject duplicates depending on DB constraints/instance policies.
    # We treat "already exists" as success.
    if resp.status_code in (409,):
        return False
    try:
        body = resp.json()
    except Exception:  # pragma: no cover
        resp.raise_for_status()
        return False

    errors = body.get("errors") or []
    msg = " ".join(str(e.get("message") or "") for e in errors).lower()
    if "unique" in msg or "already exists" in msg:
        return False

    resp.raise_for_status()
    return False


def patch_collection_meta(client: DirectusClient, collection: str, meta: Dict[str, Any]) -> None:
    # PATCH is idempotent; missing collections will error, but we want a clear signal.
    client.patch(f"/collections/{collection}", json={"meta": meta}).raise_for_status()


def ensure_nav_groups(client: DirectusClient) -> None:
    group_defs = [
        {"collection": "grp_main", "label": "Основное", "icon": "dashboard", "sort": 1},
        {"collection": "grp_promo", "label": "Промо", "icon": "confirmation_number", "sort": 2},
        {"collection": "grp_prizes", "label": "Колесо призов", "icon": "casino", "sort": 3},
        {"collection": "grp_partners", "label": "Партнерка", "icon": "handshake", "sort": 4},
        {"collection": "grp_analytics", "label": "Аналитика", "icon": "timeline", "sort": 5},
        {"collection": "grp_payments", "label": "Платежи", "icon": "payments", "sort": 6},
        {"collection": "grp_service", "label": "Служебное", "icon": "build", "sort": 7},
    ]

    for group in group_defs:
        key = group["collection"]
        # Create if missing (schema: null) or patch if exists.
        exists = client.get(f"/collections/{key}").status_code == 200
        payload_meta = {
            "icon": group["icon"],
            "note": group["label"],
            "sort": group["sort"],
            "collapse": "open",
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": group["label"]}],
        }
        if exists:
            patch_collection_meta(client, key, payload_meta)
        else:
            client.post("/collections", json={"collection": key, "schema": None, "meta": payload_meta}).raise_for_status()


def apply_collection_ux(client: DirectusClient) -> None:
    # Main navigation: align with FastAdmin structure but more informative.
    # NOTE: Do NOT hide business-critical collections; "empty admin" is often caused by hidden+no permissions.
    meta_updates: Dict[str, Dict[str, Any]] = {
        "users": {
            "group": "grp_main",
            "icon": "people",
            "note": "Пользователи, подписки, лимиты, блокировки",
            "sort": 1,
            "display_template": "{{username}} — {{full_name}}",
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Пользователи"}],
        },
        "active_tariffs": {
            "group": "grp_main",
            "icon": "subscriptions",
            "note": "Активные тарифы, LTE-лимиты и usage",
            "sort": 2,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Активные тарифы"}],
        },
        "tariffs": {
            "group": "grp_main",
            "icon": "sell",
            "note": "Справочник тарифов и цен",
            "sort": 3,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Тарифы"}],
        },
        "promo_batches": {
            "group": "grp_promo",
            "icon": "inventory_2",
            "note": "Партии промокодов",
            "sort": 1,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Партии промокодов"}],
        },
        "promo_codes": {
            "group": "grp_promo",
            "icon": "confirmation_number",
            "note": "Промокоды и эффекты (HMAC генерируется хуком)",
            "sort": 2,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Промокоды"}],
        },
        "promo_usages": {
            "group": "grp_promo",
            "icon": "history",
            "note": "История использования промокодов",
            "sort": 3,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Использование промокодов"}],
        },
        "prize_wheel_config": {
            "group": "grp_prizes",
            "icon": "casino",
            "note": "Настройки призов и вероятностей",
            "sort": 1,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Настройки призов"}],
        },
        "prize_wheel_history": {
            "group": "grp_prizes",
            "icon": "history_toggle_off",
            "note": "История выпадения призов",
            "sort": 2,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "История призов"}],
        },
        "processed_payments": {
            "group": "grp_payments",
            "icon": "payments",
            "note": "Обработанные платежи",
            "sort": 1,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Платежи"}],
        },
        "connections": {
            "group": "grp_analytics",
            "icon": "timeline",
            "note": "Подключения пользователей (для графиков)",
            "sort": 1,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Подключения"}],
        },
        "notification_marks": {
            "group": "grp_analytics",
            "icon": "notifications",
            "note": "Служебные отметки рассылок",
            "sort": 2,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Отметки уведомлений"}],
        },
        # Partners module (if present)
        "partner_withdrawals": {
            "group": "grp_partners",
            "icon": "payments",
            "note": "Заявки на вывод средств партнеров",
            "sort": 1,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Заявки на вывод"}],
        },
        "partner_qr_codes": {
            "group": "grp_partners",
            "icon": "qr_code",
            "note": "QR-коды партнеров и статистика переходов/активаций",
            "sort": 2,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "QR-коды партнеров"}],
        },
        "partner_earnings": {
            "group": "grp_partners",
            "icon": "bar_chart",
            "note": "История начислений по партнерке",
            "sort": 3,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Начисления партнеров"}],
        },
        # Service (hide from app nav by default; still accessible for admin if needed)
        "personal_discounts": {
            "group": "grp_service",
            "icon": "percent",
            "note": "Персональные скидки (служебное)",
            "sort": 1,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Персональные скидки"}],
        },
        "hwid_devices_local": {
            "group": "grp_service",
            "icon": "devices",
            "note": "Локальные HWID устройства",
            "sort": 2,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "HWID устройства"}],
        },
        "tvcode": {
            "group": "grp_service",
            "icon": "vpn_key",
            "note": "Служебные коды доступа",
            "sort": 3,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "TV-коды"}],
        },
        "admin": {
            "group": "grp_service",
            "icon": "admin_panel_settings",
            "note": "Админы FastAdmin (исторические записи)",
            "sort": 4,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Админы FastAdmin"}],
        },
        "aerich": {
            "group": "grp_service",
            "icon": "build",
            "note": "Таблица миграций Aerich",
            "sort": 5,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "Миграции Aerich"}],
        },
    }

    for collection, meta in meta_updates.items():
        # Skip missing optional collections (partner module may not exist everywhere).
        resp = client.get(f"/collections/{collection}")
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        patch_collection_meta(client, collection, meta)


def patch_field_meta(client: DirectusClient, collection: str, field: str, meta: Dict[str, Any]) -> None:
    resp = client.patch(f"/fields/{collection}/{field}", json={"meta": meta})
    # In mixed deployments, some columns might not exist; skip safely.
    if resp.status_code in (403, 404):
        return
    resp.raise_for_status()


def apply_field_notes_ru(client: DirectusClient) -> None:
    field_notes = {
        "users": {
            "lte_gb_total": "Лимит LTE в ГБ. Изменение синхронизируется с RemnaWave.",
            "expired_at": "Дата окончания подписки. Изменение синхронизируется с RemnaWave.",
            "hwid_limit": "Лимит устройств (HWID). Изменение синхронизируется с RemnaWave.",
            "balance": "Баланс пользователя в системе.",
            "is_blocked": "Блокировка пользователя.",
            "registration_date": "Дата регистрации пользователя. Используется в аналитике и алертах.",
        },
        "active_tariffs": {
            "lte_gb_total": "Общий LTE лимит для тарифа.",
            "lte_gb_used": "Использовано LTE (ГБ).",
        },
        "promo_codes": {
            "code_hmac": "Можно указать сырой код — хук преобразует в HMAC.",
            "effects": "JSON с эффектами промокода.",
        },
        "promo_usages": {
            "used_at": "Когда промокод был применен.",
        },
        "prize_wheel_config": {
            "probability": "Вероятность от 0 до 1. Сумма активных призов ≤ 1.",
            "prize_value": "Для subscription указывать число дней.",
        },
        "processed_payments": {
            "amount": "Сумма платежа. Используется в витрине и для поиска аномалий.",
            "status": "Статус обработки/зачисления платежа.",
            "processed_at": "Когда платеж был обработан и попал в систему.",
        },
    }
    field_translations = {
        "users": {
            "username": "Логин",
            "full_name": "ФИО",
            "expired_at": "Дата окончания",
            "hwid_limit": "Лимит устройств",
            "lte_gb_total": "Лимит LTE (ГБ)",
            "balance": "Баланс",
            "is_blocked": "Заблокирован",
            "registration_date": "Дата регистрации",
        },
        "active_tariffs": {
            "months": "Месяцев",
            "price": "Цена",
            "lte_gb_total": "Лимит LTE (ГБ)",
            "lte_gb_used": "Использовано LTE (ГБ)",
        },
        "promo_codes": {
            "code_hmac": "Код (HMAC)",
            "effects": "Эффекты",
            "disabled": "Отключен",
            "batch_id": "Партия",
        },
        "promo_usages": {
            "used_at": "Дата использования",
            "user_id": "Пользователь",
            "promo_code_id": "Промокод",
        },
        "prize_wheel_config": {
            "prize_type": "Тип приза",
            "prize_name": "Название",
            "prize_value": "Значение",
            "probability": "Вероятность",
        },
        "connections": {"at": "Дата подключения"},
        "processed_payments": {
            "amount": "Сумма",
            "status": "Статус",
            "processed_at": "Дата обработки",
            "payment_id": "ID платежа",
            "user_id": "Пользователь",
        },
    }

    for collection, fields in field_notes.items():
        for field, note in fields.items():
            meta_payload: Dict[str, Any] = {"note": note}
            translation = field_translations.get(collection, {}).get(field)
            if translation:
                meta_payload["translations"] = [{"language": "ru-RU", "translation": translation}]
            patch_field_meta(client, collection, field, meta_payload)


def ensure_admin_settings(client: DirectusClient) -> None:
    """
    Create a singleton collection to store adjustable thresholds for dashboard alerts/widgets.
    Idempotent and safe to run multiple times.
    """

    collection = "tvpn_admin_settings"

    if client.get(f"/collections/{collection}").status_code != 200:
        created = client.post(
            "/collections",
            json={
                "collection": collection,
                "meta": {
                    "icon": "tune",
                    "note": "Настройки алертов/виджетов для Главной (tvpn-home)",
                    "group": "grp_service",
                    "singleton": True,
                    "hidden": True,
                },
                "schema": {},
            },
        )
        # Mixed permission environments: if we can't create it, just skip gracefully.
        if created.status_code in (401, 403):
            return
        created.raise_for_status()

    # Ensure fields exist (best-effort; Directus will reject duplicates).
    def ensure_field(field: str, type_: str, schema_extra: Dict[str, Any], meta_extra: Dict[str, Any]) -> None:
        resp = client.get(f"/fields/{collection}/{field}")
        if resp.status_code == 200:
            # Keep it simple: don't overwrite if already exists.
            return
        if resp.status_code not in (404,):
            return
        created = client.post(
            f"/fields/{collection}",
            json={
                "field": field,
                "type": type_,
                "schema": schema_extra,
                "meta": meta_extra,
            },
        )
        if created.status_code in (401, 403):
            return
        # 409 duplicates are fine (race/parallel runs).
        if created.status_code == 409:
            return
        created.raise_for_status()

    ensure_field(
        "alerts_enabled",
        "boolean",
        {"default_value": True},
        {"interface": "boolean", "note": "Включать алерты на Главной"},
    )
    ensure_field(
        "reg_spike_factor",
        "float",
        {"default_value": 3.0},
        {"interface": "input", "note": "Всплеск регистраций: сегодня >= avg(7д) * factor"},
    )
    ensure_field(
        "reg_spike_min",
        "integer",
        {"default_value": 10},
        {"interface": "input", "note": "Минимум регистраций сегодня для алерта (шум-фильтр)"},
    )
    ensure_field(
        "conn_drop_factor",
        "float",
        {"default_value": 0.4},
        {"interface": "input", "note": "Падение подключений: сегодня <= avg(7д) * factor"},
    )
    ensure_field(
        "conn_drop_min_avg",
        "integer",
        {"default_value": 20},
        {"interface": "input", "note": "Минимум avg(7д) подключений, чтобы алерт считался значимым"},
    )
    ensure_field(
        "pay_spike_factor",
        "float",
        {"default_value": 2.5},
        {"interface": "input", "note": "Аномалия платежей: сумма сегодня >= avg(7д) * factor"},
    )
    ensure_field(
        "pay_spike_min_sum",
        "float",
        {"default_value": 5000.0},
        {"interface": "input", "note": "Минимальная сумма за день для алерта по платежам"},
    )
    ensure_field(
        "expiring_days",
        "integer",
        {"default_value": 7},
        {"interface": "input", "note": "Сколько дней вперед показывать “истекает подписка”"},
    )
    ensure_field(
        "suspicious_block_days",
        "integer",
        {"default_value": 3},
        {"interface": "input", "note": "Окно (дней) для виджета “подозрительные блокировки”"},
    )

    # Ensure singleton row exists.
    defaults = {
        "alerts_enabled": True,
        "reg_spike_factor": 3.0,
        "reg_spike_min": 10,
        "conn_drop_factor": 0.4,
        "conn_drop_min_avg": 20,
        "pay_spike_factor": 2.5,
        "pay_spike_min_sum": 5000.0,
        "expiring_days": 7,
        "suspicious_block_days": 3,
    }
    items = client.get(f"/items/{collection}", params={"limit": 1, "fields": "id"}).json().get("data") or []
    if not items:
        created = client.post(f"/items/{collection}", json=defaults)
        if created.status_code in (401, 403):
            return
        created.raise_for_status()

    # Ensure permissions for Manager/Viewer policies (post-create, so order doesn't matter).
    manager_policy_id = get_policy_id_by_name(client, "Manager")
    viewer_policy_id = get_policy_id_by_name(client, "Viewer")
    if manager_policy_id:
        ensure_permission(client, manager_policy_id, collection, "read")
        ensure_permission(client, manager_policy_id, collection, "update")
    if viewer_policy_id:
        ensure_permission(client, viewer_policy_id, collection, "read")


def apply_users_form_ux(client: DirectusClient) -> None:
    """
    Improve the default item (detail) form for `users`:
    - better widths (more "app-like" editing)
    - readonly fields aligned with legacy FastAdmin behavior
    - predictable ordering for key ops fields
    """

    # Readonly by legacy admin (FastAdmin): keep id + core identity immutable.
    readonly_fields = {
        "id",
        "registration_date",
        "activation_date",
        "referrals",
        "username",
        "full_name",
    }

    # Widths: help the item form become "dashboard-like" and less vertical.
    widths = {
        "id": "half",
        "username": "half",
        "full_name": "half",
        "email": "half",
        "registration_date": "half",
        "activation_date": "half",
        "expired_at": "half",
        "balance": "quarter",
        "lte_gb_total": "quarter",
        "hwid_limit": "quarter",
        "is_blocked": "quarter",
        "blocked_at": "half",
        "is_partner": "quarter",
        "custom_referral_percent": "quarter",
        "prize_wheel_attempts": "quarter",
        "is_registered": "quarter",
        "remnawave_uuid": "half",
        "active_tariff": "half",
    }

    groups = {
        # "App-like" sections for faster scanning/editing.
        "id": "Основное",
        "username": "Основное",
        "full_name": "Основное",
        "email": "Основное",
        "registration_date": "Основное",
        "activation_date": "Основное",
        "expired_at": "Подписка",
        "is_registered": "Подписка",
        "active_tariff": "Подписка",
        "balance": "Финансы",
        "lte_gb_total": "Лимиты",
        "hwid_limit": "Лимиты",
        "is_blocked": "Статус",
        "blocked_at": "Статус",
        "prize_wheel_attempts": "Операции",
        "is_partner": "Партнерка",
        "custom_referral_percent": "Партнерка",
        "remnawave_uuid": "Техника",
    }

    # Sort order (best-effort): smaller number = higher on the form.
    sort = {
        "username": 10,
        "full_name": 11,
        "email": 12,
        "registration_date": 20,
        "activation_date": 21,
        "expired_at": 22,
        "balance": 30,
        "lte_gb_total": 31,
        "hwid_limit": 32,
        "active_tariff": 33,
        "is_registered": 40,
        "is_blocked": 41,
        "blocked_at": 42,
        "is_partner": 50,
        "custom_referral_percent": 51,
        "prize_wheel_attempts": 60,
        "remnawave_uuid": 70,
    }

    for field, width in widths.items():
        meta: Dict[str, Any] = {"width": width}
        if field in readonly_fields:
            meta["readonly"] = True
        if field in sort:
            meta["sort"] = sort[field]
        if field in groups:
            meta["group"] = groups[field]
        patch_field_meta(client, "users", field, meta)


def ensure_users_presentation_dividers(client: DirectusClient) -> None:
    """
    Add visual separators to the `users` item form using Directus built-in
    `presentation-divider` interface. This turns the detail view from a long
    "wall of fields" into readable sections.
    """

    collection = "users"
    if client.get(f"/collections/{collection}").status_code != 200:
        return

    def ensure_divider(field: str, title: str, icon: str, sort: int) -> None:
        meta = {
            "interface": "presentation-divider",
            "options": {"title": title, "icon": icon},
            "special": ["alias", "no-data"],
            "width": "full",
            "sort": sort,
        }
        # Try patch first (fast path if it exists).
        resp = client.patch(f"/fields/{collection}/{field}", json={"meta": meta})
        if resp.status_code == 404:
            created = client.post(
                f"/fields/{collection}",
                json={
                    "field": field,
                    "type": "alias",
                    "schema": None,
                    "meta": meta,
                },
            )
            if created.status_code in (401, 403):
                return
            # Ignore duplicates / races.
            if created.status_code == 409:
                return
            created.raise_for_status()
            return
        if resp.status_code in (401, 403):
            return
        resp.raise_for_status()

    # Keep divider sort values between field blocks we already sorted in apply_users_form_ux().
    ensure_divider("ui_divider_overview", "Основное", "person", 1)
    ensure_divider("ui_divider_subscription", "Подписка", "event", 19)
    ensure_divider("ui_divider_limits", "Лимиты", "tune", 29)
    ensure_divider("ui_divider_status", "Статус", "shield", 39)
    ensure_divider("ui_divider_partner", "Партнерка", "groups", 49)
    ensure_divider("ui_divider_ops", "Операции", "handyman", 59)
    ensure_divider("ui_divider_tech", "Техника", "settings", 69)

    # Improve interfaces for key fields (best-effort).
    # If a field doesn't exist in the current instance, patch_field_meta will safely skip.
    patch_field_meta(client, "users", "is_blocked", {"interface": "toggle", "options": {"label": "Заблокирован"}})
    patch_field_meta(client, "users", "is_partner", {"interface": "toggle", "options": {"label": "Партнер"}})
    patch_field_meta(client, "users", "expired_at", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "registration_date", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "activation_date", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "blocked_at", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "custom_referral_percent", {"interface": "slider", "options": {"min": 0, "max": 100, "step": 1, "alwaysShowValue": True}})


def set_language_ru(client: DirectusClient) -> None:
    # Make the whole instance default to Russian and set the current user language too.
    client.patch("/settings", json={"default_language": "ru-RU"}).raise_for_status()
    client.patch("/users/me", json={"language": "ru-RU"}).raise_for_status()


def ensure_permissions_baseline(client: DirectusClient) -> None:
    role_map = get_role_map(client)
    admin_role = role_map.get("Administrator")
    if not admin_role:
        raise SystemExit("Administrator role not found (Directus is not initialized?)")
    admin_role_id = str(admin_role["id"])

    # Bootstrap: some instances don't allow creating policies until the current policy
    # explicitly has system permissions. We grant them to the Administrator policy.
    admin_policy_id = get_policy_id_by_name(client, "Administrator")
    if admin_policy_id:
        for collection in ("directus_policies", "directus_roles", "directus_permissions", "directus_access"):
            for action in ("read", "create", "update", "delete"):
                ensure_permission(client, admin_policy_id, collection, action)

    manager_role_id = ensure_role(
        client,
        "Manager",
        icon="manage_accounts",
        description="Операционная роль: управление пользователями/тарифами/промо/призами",
    )
    viewer_role_id = ensure_role(
        client,
        "Viewer",
        icon="visibility",
        description="Просмотр витрины без правок",
    )

    # Directus v11 permissions are assigned to policies (not roles).
    manager_policy_id = ensure_policy_for_role(
        client,
        manager_role_id,
        name="Manager",
        icon="manage_accounts",
        description="App access + CRUD на бизнес-коллекции (без системных настроек)",
        admin_access=False,
        app_access=True,
    )
    viewer_policy_id = ensure_policy_for_role(
        client,
        viewer_role_id,
        name="Viewer",
        icon="visibility",
        description="App access + read-only витрина",
        admin_access=False,
        app_access=True,
    )

    # Administrator typically has admin_access via its policy; don't spam-create permissions.
    # Still, we keep the role around as a sanity check.
    _ = admin_role_id

    existing_collections = set(list_collections(client))

    manager_rw = {
        "users",
        "active_tariffs",
        "tariffs",
        "promo_batches",
        "promo_codes",
        "promo_usages",
        "prize_wheel_config",
        "prize_wheel_history",
        "processed_payments",
        # Partners (optional)
        "partner_withdrawals",
    }
    manager_ro = {
        "connections",
    }
    viewer_ro = {
        "users",
        "active_tariffs",
        "tariffs",
        "connections",
        "processed_payments",
    }

    # Narrow to collections that actually exist in the current instance.
    manager_rw = {c for c in manager_rw if c in existing_collections}
    manager_ro = {c for c in manager_ro if c in existing_collections}
    viewer_ro = {c for c in viewer_ro if c in existing_collections}

    for collection in sorted(manager_rw):
        for action in ("read", "create", "update", "delete"):
            ensure_permission(client, manager_policy_id, collection, action)
    for collection in sorted(manager_ro):
        ensure_permission(client, manager_policy_id, collection, "read")

    for collection in sorted(viewer_ro):
        ensure_permission(client, viewer_policy_id, collection, "read")

    # Dashboard settings (singleton): allow Manager to read/update, Viewer read-only.
    if client.get("/collections/tvpn_admin_settings").status_code == 200:
        ensure_permission(client, manager_policy_id, "tvpn_admin_settings", "read")
        ensure_permission(client, manager_policy_id, "tvpn_admin_settings", "update")
        ensure_permission(client, viewer_policy_id, "tvpn_admin_settings", "read")


def ensure_insights_dashboard(client: DirectusClient) -> None:
    # Reuse the existing logic from scripts/directus_insights_setup.py, but inline to keep this script standalone.
    dashboards = client.get("/dashboards", params={"limit": 200})
    dashboards.raise_for_status()
    existing = next((d for d in dashboards.json().get("data", []) if d.get("name") == "Главный дашборд"), None)
    if existing:
        dashboard_id = existing["id"]
    else:
        created = client.post(
            "/dashboards",
            json={"name": "Главный дашборд", "icon": "dashboard", "note": "Ключевые метрики проекта"},
        )
        created.raise_for_status()
        dashboard_id = created.json()["data"]["id"]

    panels = client.get("/panels", params={"filter[dashboard][_eq]": dashboard_id, "limit": 200})
    panels.raise_for_status()
    existing_panels = {p["name"]: p for p in panels.json().get("data", [])}

    # Only panels based on core collections to avoid brittleness.
    panel_defs = [
        {
            "name": "Всего пользователей",
            "type": "metric",
            "icon": "people",
            "position_x": 1,
            "position_y": 1,
            "width": 4,
            "height": 4,
            "options": {"collection": "users", "field": "id", "function": "count"},
        },
        {
            "name": "Активных тарифов",
            "type": "metric",
            "icon": "subscriptions",
            "position_x": 5,
            "position_y": 1,
            "width": 4,
            "height": 4,
            "options": {"collection": "active_tariffs", "field": "id", "function": "count"},
        },
        {
            "name": "Промокодов использовано",
            "type": "metric",
            "icon": "confirmation_number",
            "position_x": 9,
            "position_y": 1,
            "width": 4,
            "height": 4,
            "options": {"collection": "promo_usages", "field": "id", "function": "count"},
        },
        {
            "name": "Регистрации пользователей (90 дней)",
            "type": "time-series",
            "icon": "timeline",
            "position_x": 1,
            "position_y": 5,
            "width": 6,
            "height": 8,
            "options": {
                "collection": "users",
                "dateField": "registration_date",
                "valueField": "id",
                "function": "count",
                "precision": "day",
                "range": "90 days",
                "color": "#3B82F6",
            },
        },
        {
            "name": "Подключения (90 дней)",
            "type": "time-series",
            "icon": "wifi",
            "position_x": 7,
            "position_y": 5,
            "width": 6,
            "height": 8,
            "options": {
                "collection": "connections",
                "dateField": "at",
                "valueField": "id",
                "function": "count",
                "precision": "day",
                "range": "90 days",
                "color": "#10B981",
            },
        },
    ]

    for panel in panel_defs:
        if panel["name"] in existing_panels:
            continue
        payload = {
            "dashboard": dashboard_id,
            "name": panel["name"],
            "icon": panel["icon"],
            "show_header": True,
            "type": panel["type"],
            "position_x": panel["position_x"],
            "position_y": panel["position_y"],
            "width": panel["width"],
            "height": panel["height"],
            "options": panel["options"],
        }
        # Skip panels if referenced collections don't exist yet.
        collection = panel["options"].get("collection")
        if collection:
            exists = client.get(f"/collections/{collection}").status_code == 200
            if not exists:
                continue
        client.post("/panels", json=payload).raise_for_status()


def ensure_nav_group_permissions(client: DirectusClient) -> None:
    role_map = get_role_map(client)
    manager_role = role_map.get("Manager")
    viewer_role = role_map.get("Viewer")

    policy_ids: list[str] = []
    if manager_role:
        pid = get_policy_id_by_name(client, "Manager") or get_primary_policy_id_for_role(client, str(manager_role["id"]))
        if pid:
            policy_ids.append(str(pid))
    if viewer_role:
        pid = get_policy_id_by_name(client, "Viewer") or get_primary_policy_id_for_role(client, str(viewer_role["id"]))
        if pid:
            policy_ids.append(str(pid))

    if not policy_ids:
        return

    groups = [
        "grp_main",
        "grp_promo",
        "grp_prizes",
        "grp_partners",
        "grp_analytics",
        "grp_payments",
        "grp_service",
    ]
    for grp in groups:
        if client.get(f"/collections/{grp}").status_code != 200:
            continue
        for policy_id in policy_ids:
            ensure_permission(client, policy_id, grp, "read")


def ensure_role_presets(client: DirectusClient) -> None:
    # Presets/bookmarks define the UX of listing pages (fields, widths, filters, sorts).
    role_map = get_role_map(client)
    admin_role = role_map.get("Administrator")
    manager = role_map.get("Manager")
    viewer = role_map.get("Viewer")
    if not admin_role and not manager and not viewer:
        return

    existing_resp = client.get("/presets", params={"limit": 1000})
    existing_resp.raise_for_status()
    existing = existing_resp.json().get("data", []) or []

    def ensure_tabular_fields_include_pk(preset: Dict[str, Any], *, pk_field: str = "id") -> Optional[Dict[str, Any]]:
        """
        Directus list UX depends on items having a primary key.
        If tabular `fields` omit the PK, item navigation on row-click may silently stop working.
        """
        layout_query = preset.get("layout_query")
        if not isinstance(layout_query, dict):
            return None
        tabular = layout_query.get("tabular")
        if not isinstance(tabular, dict):
            return None
        fields = tabular.get("fields")
        if not isinstance(fields, list):
            return None
        # Keep existing order and just prepend PK (Directus doesn't support "hidden internal fields").
        if pk_field in fields:
            return None
        new_fields = [pk_field, *fields]
        new_tabular = {**tabular, "fields": new_fields}
        new_layout_query = {**layout_query, "tabular": new_tabular}
        return new_layout_query

    def ensure_cards_fields_include_pk(
        preset: Dict[str, Any],
        *,
        pk_field: str = "id",
        required_fields: Optional[list[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Same idea as tabular: cards list also needs a PK included in the fetched fields.
        Without it, click-to-open item view can break and cards can render inconsistently.
        """
        layout_query = preset.get("layout_query")
        if not isinstance(layout_query, dict):
            return None
        cards = layout_query.get("cards")
        if not isinstance(cards, dict):
            return None
        fields = cards.get("fields")
        if not isinstance(fields, list):
            return None

        # If the preset somehow got saved with an empty fields list, cards will look "blank".
        # Rehydrate it with a minimal set.
        if not fields:
            fields = []

        want: list[str] = []
        if required_fields:
            want.extend([f for f in required_fields if isinstance(f, str) and f])
        want.append(pk_field)

        changed = False
        new_fields = list(fields)
        for f in reversed(want):
            if f not in new_fields:
                new_fields.insert(0, f)
                changed = True

        if not changed:
            return None

        new_cards = {**cards, "fields": new_fields}
        new_layout_query = {**layout_query, "cards": new_cards}
        return new_layout_query

    # Auto-heal broken personal presets for `users`.
    # We normally avoid overwriting user-level presets, but missing PK breaks navigation entirely.
    for p in list(existing):
        if p.get("collection") != "users":
            continue
        preset_id = p.get("id")
        if not preset_id:
            continue
        layout = p.get("layout")
        if layout == "tabular":
            new_layout_query = ensure_tabular_fields_include_pk(p, pk_field="id")
        elif layout == "cards":
            new_layout_query = ensure_cards_fields_include_pk(
                p,
                pk_field="id",
                required_fields=["username", "full_name"],
            )
        else:
            continue
        if not new_layout_query:
            continue
        client.patch(f"/presets/{preset_id}", json={"layout_query": new_layout_query}).raise_for_status()
        p["layout_query"] = new_layout_query

    def upsert_preset(payload: Dict[str, Any]) -> None:
        collection = payload.get("collection")
        if collection and client.get(f"/collections/{collection}").status_code != 200:
            return

        key = (
            payload.get("role"),
            payload.get("user"),
            payload.get("collection"),
            payload.get("bookmark"),
        )
        found = next(
            (
                p
                for p in existing
                if (
                    p.get("role"),
                    p.get("user"),
                    p.get("collection"),
                    p.get("bookmark"),
                )
                == key
            ),
            None,
        )
        if found:
            client.patch(f"/presets/{found['id']}", json=payload).raise_for_status()
            return
        created = client.post("/presets", json=payload)
        created.raise_for_status()
        existing.append(created.json().get("data"))

    # IMPORTANT: always include the primary key in tabular fields.
    # If `id` is missing, Directus list rows come back without PK and
    # selection checkboxes can behave as "select all" (all rows share undefined PK).
    users_tabular_fields = [
        "id",
        "username",
        "full_name",
        "balance",
        "expired_at",
        "hwid_limit",
        "lte_gb_total",
        "is_blocked",
        "registration_date",
    ]
    users_widths = {
        "id": 140,
        "username": 160,
        "full_name": 220,
        "balance": 110,
        "expired_at": 160,
        "hwid_limit": 150,
        "lte_gb_total": 150,
        "is_blocked": 130,
        "registration_date": 170,
    }

    # Prefer making presets available for Administrator too, because most real ops are done as admin.
    target_roles: list[Dict[str, Any]] = []
    if admin_role:
        target_roles.append(admin_role)
    if manager:
        target_roles.append(manager)

    for role in target_roles:
        rid = role["id"]
        # Default list view for users (role-level)
        upsert_preset(
            {
                "bookmark": None,
                "user": None,
                "role": rid,
                "collection": "users",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": users_tabular_fields, "sort": "-registration_date"}},
                "layout_options": {"tabular": {"widths": users_widths}},
                "search": None,
                "filter": None,
                "icon": "bookmark",
                "color": None,
            }
        )
        # Bookmarks: blocked users, cards view, disabled promo, latest payments.
        upsert_preset(
            {
                "bookmark": "Пользователи: заблокированные",
                "user": None,
                "role": rid,
                "collection": "users",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": users_tabular_fields, "sort": "-registration_date"}},
                "layout_options": {"tabular": {"widths": users_widths}},
                "filter": {"is_blocked": {"_eq": True}},
                "icon": "bookmark",
                "color": "#EF4444",
            }
        )
        upsert_preset(
            {
                "bookmark": "Пользователи: истекает скоро",
                "user": None,
                "role": rid,
                "collection": "users",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": users_tabular_fields, "sort": "expired_at"}},
                "layout_options": {"tabular": {"widths": users_widths}},
                "filter": {"expired_at": {"_nnull": True}},
                "icon": "bookmark",
                "color": "#F59E0B",
            }
        )
        upsert_preset(
            {
                "bookmark": "Пользователи: топ по балансу",
                "user": None,
                "role": rid,
                "collection": "users",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": users_tabular_fields, "sort": "-balance"}},
                "layout_options": {"tabular": {"widths": users_widths}},
                "filter": None,
                "icon": "bookmark",
                "color": "#10B981",
            }
        )
        upsert_preset(
            {
                "bookmark": "Пользователи: карточки",
                "user": None,
                "role": rid,
                "collection": "users",
                "layout": "cards",
                "layout_query": {
                    "cards": {
                        "sort": "-registration_date",
                        # IMPORTANT: include PK; without it cards navigation can break.
                        "fields": ["id", "username", "full_name", "expired_at", "lte_gb_total", "is_blocked", "registration_date"],
                    }
                },
                "layout_options": {
                    "cards": {
                        "title": "{{ username }}",
                        "subtitle": "{{ full_name }}",
                        "icon": "person",
                        "size": 2,
                    }
                },
                "filter": None,
                "icon": "bookmark",
                "color": "#3B82F6",
            }
        )
        upsert_preset(
            {
                "bookmark": "Промо: отключенные",
                "user": None,
                "role": rid,
                "collection": "promo_codes",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["name", "code_hmac", "disabled", "expires_at", "max_activations", "per_user_limit", "created_at"],
                        "sort": "-created_at",
                    }
                },
                "layout_options": {"tabular": {"widths": {"name": 180, "code_hmac": 260, "disabled": 120, "expires_at": 160}}},
                "filter": {"disabled": {"_eq": True}},
                "icon": "bookmark",
                "color": "#F59E0B",
            }
        )
        upsert_preset(
            {
                "bookmark": "Платежи: последние",
                "user": None,
                "role": rid,
                "collection": "processed_payments",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["payment_id", "user_id", "amount", "status", "processed_at"],
                        "sort": "-processed_at",
                    }
                },
                "layout_options": {"tabular": {"widths": {"payment_id": 220, "user_id": 160, "amount": 120, "status": 140, "processed_at": 180}}},
                "filter": None,
                "icon": "bookmark",
                "color": "#8B5CF6",
            }
        )
        upsert_preset(
            {
                "bookmark": "Платежи: крупные",
                "user": None,
                "role": rid,
                "collection": "processed_payments",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["payment_id", "user_id", "amount", "status", "processed_at"],
                        "sort": "-amount",
                    }
                },
                "layout_options": {"tabular": {"widths": {"payment_id": 220, "user_id": 160, "amount": 120, "status": 140, "processed_at": 180}}},
                "filter": {"amount": {"_gt": 0}},
                "icon": "bookmark",
                "color": "#10B981",
            }
        )

    if viewer:
        rid = viewer["id"]
        upsert_preset(
            {
                "bookmark": None,
                "user": None,
                "role": rid,
                "collection": "users",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": ["id", "username", "full_name", "registration_date", "expired_at", "is_blocked"], "sort": "-registration_date"}},
                "layout_options": {"tabular": {"widths": {"id": 140, "username": 180, "full_name": 240, "registration_date": 180, "expired_at": 160, "is_blocked": 130}}},
                "filter": None,
                "icon": "bookmark",
                "color": None,
            }
        )

    # Also patch a default user-level preset for the current admin user
    # to avoid conflicts with any existing personal presets.
    me = client.get("/users/me", params={"fields": "id"}).json().get("data") or {}
    me_id = me.get("id")
    if me_id:
        upsert_preset(
            {
                "bookmark": None,
                "user": me_id,
                "role": None,
                "collection": "users",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": users_tabular_fields, "sort": "-registration_date"}},
                "layout_options": {"tabular": {"widths": users_widths}},
                "search": None,
                "filter": None,
                "icon": "bookmark",
                "color": None,
            }
        )


def ensure_extension_enabled(client: DirectusClient, extension_name: str) -> None:
    # Extension enabling is stored in Directus metadata. We enable by extension ID.
    resp = client.get("/extensions")
    if resp.status_code in (401, 403):
        return
    resp.raise_for_status()
    exts = resp.json().get("data") or []
    target = None
    for ext in exts:
        schema = ext.get("schema") or {}
        meta = ext.get("meta") or {}
        if schema.get("name") == extension_name or meta.get("folder") == extension_name:
            target = ext
            break
    if not target:
        return
    meta = target.get("meta") or {}
    if meta.get("enabled") is True:
        return
    ext_id = target.get("id")
    if not ext_id:
        return
    client.patch(f"/extensions/{ext_id}", json={"meta": {"enabled": True}}).raise_for_status()


def main() -> None:
    if load_dotenv:
        load_dotenv()

    base_url = env("DIRECTUS_URL")  # e.g. http://37.230.114.122:8055
    email = env("DIRECTUS_ADMIN_EMAIL")
    password = env("DIRECTUS_ADMIN_PASSWORD")

    auth = login(base_url, email, password)
    client = DirectusClient(auth)

    set_language_ru(client)
    ensure_permissions_baseline(client)
    ensure_nav_groups(client)
    ensure_nav_group_permissions(client)
    apply_collection_ux(client)
    apply_field_notes_ru(client)
    apply_users_form_ux(client)
    ensure_users_presentation_dividers(client)
    ensure_admin_settings(client)
    ensure_insights_dashboard(client)
    ensure_role_presets(client)
    # Enable optional app extensions if they are present on disk
    ensure_extension_enabled(client, "tvpn-home")

    print("Directus super-setup completed successfully.")


if __name__ == "__main__":
    main()


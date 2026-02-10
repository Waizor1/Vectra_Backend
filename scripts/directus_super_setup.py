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
        },
        "active_tariffs": {
            "lte_gb_total": "Общий LTE лимит для тарифа.",
            "lte_gb_used": "Использовано LTE (ГБ).",
        },
        "promo_codes": {
            "code_hmac": "Можно указать сырой код — хук преобразует в HMAC.",
            "effects": "JSON с эффектами промокода.",
        },
        "prize_wheel_config": {
            "probability": "Вероятность от 0 до 1. Сумма активных призов ≤ 1.",
            "prize_value": "Для subscription указывать число дней.",
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
        "prize_wheel_config": {
            "prize_type": "Тип приза",
            "prize_name": "Название",
            "prize_value": "Значение",
            "probability": "Вероятность",
        },
        "connections": {"at": "Дата подключения"},
    }

    for collection, fields in field_notes.items():
        for field, note in fields.items():
            meta_payload: Dict[str, Any] = {"note": note}
            translation = field_translations.get(collection, {}).get(field)
            if translation:
                meta_payload["translations"] = [{"language": "ru-RU", "translation": translation}]
            patch_field_meta(client, collection, field, meta_payload)


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
    # Bookmarks are optional but make UX significantly better for managers.
    role_map = get_role_map(client)
    manager = role_map.get("Manager")
    viewer = role_map.get("Viewer")
    if not manager and not viewer:
        return

    # We'll create a couple of practical bookmarks with minimal structure (avoid layout brittleness).
    bookmarks = []
    if manager:
        bookmarks += [
            {
                "bookmark": "Пользователи: заблокированные",
                "role": manager["id"],
                "collection": "users",
                "layout": "tabular",
                "filters": [{"key": "1", "field": "is_blocked", "operator": "eq", "value": True}],
            },
            {
                "bookmark": "Промо: отключенные",
                "role": manager["id"],
                "collection": "promo_codes",
                "layout": "tabular",
                "filters": [{"key": "1", "field": "disabled", "operator": "eq", "value": True}],
            },
        ]
    if viewer:
        bookmarks += [
            {
                "bookmark": "Пользователи: последние",
                "role": viewer["id"],
                "collection": "users",
                "layout": "tabular",
                "layout_query": {"tabular": {"sort": "-registration_date"}},
                "filters": [],
            }
        ]

    # Keep idempotency: create only if missing.
    existing_resp = client.get("/presets", params={"limit": 1000})
    existing_resp.raise_for_status()
    existing = {(p.get("role"), p.get("collection"), p.get("bookmark")) for p in existing_resp.json().get("data", [])}

    for preset in bookmarks:
        key = (preset.get("role"), preset.get("collection"), preset.get("bookmark"))
        if key in existing:
            continue
        # Skip if collection doesn't exist (eg partner module not yet installed)
        collection = preset.get("collection")
        if collection and client.get(f"/collections/{collection}").status_code != 200:
            continue
        client.post("/presets", json=preset).raise_for_status()


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
    ensure_insights_dashboard(client)
    ensure_role_presets(client)
    # Enable optional app extensions if they are present on disk
    ensure_extension_enabled(client, "tvpn-home")

    print("Directus super-setup completed successfully.")


if __name__ == "__main__":
    main()


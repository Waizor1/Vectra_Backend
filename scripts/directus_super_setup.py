from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

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

    def delete(self, path: str) -> requests.Response:
        return self.session.delete(
            f"{self.auth.base_url}{path}",
            headers=self.auth.headers,
            timeout=self.timeout,
        )


def _phase_pause() -> None:
    """
    Small pause between heavy setup phases to reduce burst pressure on VPS/Directus.
    Tunable via env var in seconds; defaults to 0.2s.
    """
    raw = os.getenv("DIRECTUS_SUPER_SETUP_PHASE_PAUSE", "0.2")
    try:
        delay = float(raw)
    except ValueError:
        delay = 0.2
    if delay > 0:
        time.sleep(delay)


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
    target_fields = fields or ["*"]
    target_permissions = permissions or {}

    # Upsert behavior: if permission row already exists, normalize it to requested shape.
    existing = client.get(
        "/permissions",
        params={
            "filter[policy][_eq]": policy_id,
            "filter[collection][_eq]": collection,
            "filter[action][_eq]": action,
            "fields": "id,fields,permissions,validation,presets",
            "limit": 200,
        },
    )
    if existing.status_code == 200:
        rows = existing.json().get("data") or []
        if rows:
            # Normalize first row and remove duplicates to avoid unpredictable
            # behavior when many permission rows match the same policy/action.
            first_row = rows[0]
            perm_id = first_row.get("id")
            if perm_id is not None:
                patch_payload = {
                    "fields": target_fields,
                    "permissions": target_permissions,
                    "validation": validation,
                    "presets": presets,
                }
                patched = client.patch(f"/permissions/{perm_id}", json=patch_payload)
                if patched.ok:
                    for duplicate in rows[1:]:
                        duplicate_id = duplicate.get("id")
                        if duplicate_id is None:
                            continue
                        deleted = client.delete(f"/permissions/{duplicate_id}")
                        # Best-effort dedupe. If delete is blocked, keep going.
                        if deleted.status_code in (401, 403, 404):
                            continue
                    return False
                # If patch is rejected in this instance, continue with create path below.

    payload: Dict[str, Any] = {
        "policy": policy_id,
        "collection": collection,
        "action": action,
        "fields": target_fields,
        "permissions": target_permissions,
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
        {"collection": "grp_main", "label": "Основное", "icon": "dashboard", "sort": 1, "collapse": "open"},
        {"collection": "grp_promo", "label": "Промо", "icon": "confirmation_number", "sort": 2, "collapse": "closed"},
        {"collection": "grp_prizes", "label": "Колесо призов", "icon": "casino", "sort": 3, "collapse": "closed"},
        {"collection": "grp_partners", "label": "Партнерка", "icon": "handshake", "sort": 4, "collapse": "closed"},
        {"collection": "grp_analytics", "label": "Аналитика", "icon": "timeline", "sort": 5, "collapse": "closed"},
        {"collection": "grp_payments", "label": "Платежи", "icon": "payments", "sort": 6, "collapse": "closed"},
        {"collection": "grp_service", "label": "Служебное", "icon": "build", "sort": 7, "collapse": "closed"},
    ]

    for group in group_defs:
        key = group["collection"]
        # Create if missing (schema: null) or patch if exists.
        exists = client.get(f"/collections/{key}").status_code == 200
        payload_meta = {
            "icon": group["icon"],
            "note": group["label"],
            "sort": group["sort"],
            "collapse": group["collapse"],
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
            "note": "Тарифные карточки: финальные цены, лимиты устройств и правила семейного плана",
            "sort": 3,
            "display_template": "{{order}}. {{name}} — {{months}} мес",
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Тарифы"}],
        },
        "family_members": {
            "group": "grp_main",
            "icon": "family_restroom",
            "note": "Состав семей: owner/member, статусы и выделенные устройства",
            "sort": 4,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Участники семьи"}],
        },
        "family_invites": {
            "group": "grp_main",
            "icon": "person_add",
            "note": "Семейные инвайты и их статусы",
            "sort": 5,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Инвайты семьи"}],
        },
        "family_devices": {
            "group": "grp_main",
            "icon": "devices",
            "note": "Устройства внутри семейной подписки",
            "sort": 6,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Семейные устройства"}],
        },
        "family_audit_logs": {
            "group": "grp_main",
            "icon": "history",
            "note": "Аудит семейных действий и аномалий",
            "sort": 7,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Аудит семьи"}],
        },
        "in_app_notifications": {
            "group": "grp_main",
            "icon": "notifications",
            "note": "Всплывающие уведомления в Mini App",
            "sort": 8,
            "hidden": False,
            "display_template": "{{title}}",
            "translations": [{"language": "ru-RU", "translation": "In-App уведомления"}],
        },
        "promo_batches": {
            "group": "grp_promo",
            "icon": "inventory_2",
            "note": "Партии промокодов для группировки и аудита. Создайте партию перед добавлением кодов.",
            "sort": 1,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Партии промокодов"}],
        },
        "promo_codes": {
            "group": "grp_promo",
            "icon": "confirmation_number",
            "note": "Промокоды и эффекты. Введите сырой код — хук преобразует в HMAC. Используйте закладки: активные / отключённые / истёкшие.",
            "sort": 2,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Промокоды"}],
        },
        "promo_usages": {
            "group": "grp_promo",
            "icon": "history",
            "note": "История активаций промокодов пользователями. Связь: промокод → пользователь.",
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
        "error_reports": {
            "group": "grp_service",
            "icon": "bug_report",
            "note": "Логи frontend ошибок и статус обработки баг-репортов",
            "sort": 1,
            "hidden": False,
            "translations": [{"language": "ru-RU", "translation": "Логи ошибок"}],
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
        "notification_views": {
            "group": "grp_service",
            "icon": "visibility",
            "note": "История показов уведомлений",
            "sort": 6,
            "hidden": True,
            "translations": [{"language": "ru-RU", "translation": "История показов уведомлений"}],
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
    # Transient upstream/proxy errors may happen on production (502/503/504).
    # Retry a few times to keep setup idempotent and resilient.
    last_resp: Optional[requests.Response] = None
    for attempt in range(3):
        resp = client.patch(f"/fields/{collection}/{field}", json={"meta": meta})
        last_resp = resp
        # In mixed deployments, some columns might not exist; skip safely.
        if resp.status_code in (403, 404):
            return
        if resp.status_code not in (502, 503, 504):
            resp.raise_for_status()
            return
        # Exponential-ish backoff: 0.5s, 1.0s, 1.5s
        time.sleep(0.5 * (attempt + 1))
    if last_resp is not None:
        last_resp.raise_for_status()


def apply_field_notes_ru(client: DirectusClient) -> None:
    field_notes = {
        "users": {
            "id": "Внутренний ID пользователя в Directus.",
            "is_admin": "Дает расширенные права в приложении. Включать только для доверенных сотрудников.",
            "username": "Уникальный логин пользователя.",
            "full_name": "Отображаемое имя/ФИО пользователя.",
            "created_at": "Дата создания записи в системе.",
            "email": "Email для связи и восстановления доступа.",
            "language_code": "Предпочитаемый язык пользователя.",
            "lte_gb_total": "Лимит LTE в ГБ. Изменение синхронизируется с RemnaWave.",
            "expired_at": "Дата окончания подписки. Изменение синхронизируется с RemnaWave.",
            "hwid_limit": "Лимит устройств (HWID). Изменение синхронизируется с RemnaWave.",
            "balance": "Баланс пользователя в системе.",
            "is_blocked": "Блокировка пользователя.",
            "registration_date": "Дата регистрации пользователя. Используется в аналитике и алертах.",
            "is_registered": "Флаг завершенной регистрации в боте/приложении.",
            "is_subscribed": "Есть активная подписка на текущий момент.",
            "is_trial": "Пользователь находится на пробном периоде.",
            "used_trial": "Пробный период уже был использован ранее.",
            "is_partner": "Участник партнерской программы.",
            "custom_referral_percent": "Индивидуальный процент партнерского вознаграждения.",
            "referred_by": "Родитель-реферер. Поле кликабельно для быстрого перехода.",
            "referrals": "Количество приглашенных рефералов.",
            "referral_bonus_days_total": "Сумма бонусных дней за реферальную активность.",
            "referral_first_payment_rewarded": "Награда за первый платеж реферала уже начислена.",
            "active_tariff": "Активный тариф пользователя (связь).",
            "active_tariff_id": "ID активного тарифа. Поле для быстрого перехода к тарифу.",
            "utm": "UTM-метка источника привлечения.",
            "renew_id": "Внешний идентификатор для продления.",
            "prize_wheel_attempts": "Доступное число попыток в колесе призов.",
            "blocked_at": "Дата и время последней блокировки.",
            "connected_at": "Последняя активность подключения.",
            "last_hwid_reset": "Когда в последний раз выполнялся сброс HWID.",
            "last_failed_message_at": "Когда в последний раз была ошибка отправки сообщения.",
            "failed_message_count": "Сколько раз не удалось отправить сообщение пользователю.",
            "familyurl": "Ссылка для семейного приглашения.",
            "remnawave_uuid": "UUID пользователя в RemnaWave.",
        },
        "active_tariffs": {
            "lte_gb_total": "Общий LTE лимит для тарифа.",
            "lte_gb_used": "Использовано LTE (ГБ).",
        },
        "tariffs": {
            "is_active": "Если выключить, тариф не будет доступен для новых покупок и пропадет с витрины фронтенда.",
            "base_price": "Базовая цена за 1 устройство (служебный расчетный параметр). Обновляется автоматически, если указана финальная цена карточки.",
            "progressive_multiplier": "Множитель прогрессии цены. Если заданы финальные цены обычной и семейной карточки, рассчитывается автоматически.",
            "devices_limit_default": "Лимит устройств для обычной карточки тарифа.",
            "devices_limit_family": "Лимит устройств для семейной карточки (обычно 12 месяцев).",
            "family_plan_enabled": "Включает/выключает показ семейной карточки для этого тарифа.",
            "final_price_default": "Финальная цена обычной карточки (в рублях). Рекомендуем редактировать это поле, а не base_price.",
            "final_price_family": "Финальная цена семейной карточки (в рублях). Используется вместе с family_plan_enabled и devices_limit_family.",
            "months": "Срок тарифа в месяцах. На витрине отображаются карточки 1/3/6/12 месяцев.",
            "name": "Название карточки тарифа в админке и внутренних данных.",
            "order": "Порядок отображения тарифа на витрине.",
        },
        "promo_batches": {
            "title": "Название партии/кампании. Отображается в списках.",
            "notes": "Заметки по партии: цель, канал распространения и т.п.",
        },
        "promo_codes": {
            "name": "Человекочитаемое имя для админки (не сам код).",
            "code_hmac": "Введите сырой промокод — хук преобразует в HMAC. Или вставьте готовый 64-символьный hex.",
            "effects": "JSON с эффектами: extend_days, discount_percent, add_hwid и др.",
            "batch_id": "Партия для группировки. Опционально.",
            "max_activations": "Максимум активаций всего. 0 = без лимита.",
            "per_user_limit": "Сколько раз один пользователь может активировать.",
            "expires_at": "Дата истечения (включительно). Пусто = бессрочно.",
            "disabled": "Принудительное отключение. Отключённые коды не принимаются.",
        },
        "promo_usages": {
            "promo_code_id": "Какой промокод был применён.",
            "user_id": "Кто применил промокод.",
            "used_at": "Когда промокод был активирован.",
            "context": "Доп. контекст (payment_id и т.п.).",
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
        "error_reports": {
            "event_id": "Технический идентификатор события на клиенте.",
            "code": "Человекочитаемый код ошибки для поддержки.",
            "type": "Классификация ошибки (render/runtime/network/service).",
            "message": "Текст ошибки из клиентского приложения.",
            "route": "Маршрут приложения на момент сбоя.",
            "href": "Полный URL страницы в момент сбоя.",
            "user_id": "ID пользователя (если удалось определить).",
            "triage_severity": "Критичность бага (SLA): low / medium / high / critical.",
            "triage_status": "Статус обработки баг-репорта: new / in_progress / resolved.",
            "triage_owner": "Кто обрабатывает (ник/имя/ответственный).",
            "triage_note": "Комментарий по разбору и фиксу.",
            "triage_due_at": "SLA-дедлайн: если до этого времени не взят в работу, репорт просрочен.",
            "triage_updated_at": "Когда в последний раз меняли triage-статус/комментарий.",
            "created_at": "Когда запись лога была создана в БД.",
            "reported_at": "Время ошибки на клиенте (по часам пользователя).",
        },
        "in_app_notifications": {
            "title": "Заголовок уведомления.",
            "body": "Текст уведомления. Поддерживает многострочный текст.",
            "start_at": "Дата/время начала показа уведомления (включительно).",
            "end_at": "Дата/время окончания показа уведомления (включительно).",
            "max_per_user": "Максимум показов на пользователя. Пусто = без лимита.",
            "max_per_session": "Максимум показов на сессию. Пусто = без лимита.",
            "auto_hide_seconds": "Авто-скрытие через N секунд. Пусто = ручное закрытие.",
            "is_active": "Активно ли уведомление. Неактивные не показываются.",
            "created_at": "Дата создания записи.",
            "updated_at": "Дата последнего изменения.",
        },
        "notification_views": {
            "user_id": "Пользователь, которому было показано уведомление.",
            "notification_id": "Уведомление, которое было показано.",
            "session_id": "Идентификатор сессии показа.",
            "viewed_at": "Дата и время показа уведомления.",
        },
    }
    field_translations = {
        "users": {
            "id": "ID",
            "is_admin": "Администратор",
            "username": "Логин",
            "full_name": "ФИО",
            "email": "Email",
            "language_code": "Язык",
            "utm": "UTM-метка",
            "renew_id": "ID продления",
            "created_at": "Создан",
            "activation_date": "Активация",
            "connected_at": "Последнее подключение",
            "expired_at": "Дата окончания",
            "hwid_limit": "Лимит устройств",
            "lte_gb_total": "Лимит LTE (ГБ)",
            "balance": "Баланс",
            "is_blocked": "Заблокирован",
            "blocked_at": "Дата блокировки",
            "is_registered": "Зарегистрирован",
            "is_subscribed": "Подписан",
            "is_trial": "Триал",
            "used_trial": "Триал использован",
            "is_partner": "Партнер",
            "custom_referral_percent": "Партнерский %",
            "referred_by": "Чей реферал",
            "referrals": "Рефералы",
            "referral_bonus_days_total": "Бонусные дни",
            "referral_first_payment_rewarded": "Награда за 1-й платеж",
            "active_tariff": "Активный тариф",
            "active_tariff_id": "Активный тариф (ID)",
            "prize_wheel_attempts": "Попытки колеса",
            "last_hwid_reset": "Сброс HWID",
            "last_failed_message_at": "Последняя ошибка сообщений",
            "failed_message_count": "Ошибки сообщений",
            "familyurl": "Семейная ссылка",
            "remnawave_uuid": "UUID RemnaWave",
            "registration_date": "Дата регистрации",
        },
        "active_tariffs": {
            "months": "Месяцев",
            "price": "Цена",
            "lte_gb_total": "Лимит LTE (ГБ)",
            "lte_gb_used": "Использовано LTE (ГБ)",
        },
        "tariffs": {
            "is_active": "Активен",
            "name": "Название",
            "months": "Месяцев",
            "base_price": "Базовая цена (служебная)",
            "progressive_multiplier": "Множитель прогрессии",
            "devices_limit_default": "Лимит устройств (обычный)",
            "devices_limit_family": "Лимит устройств (семейный)",
            "family_plan_enabled": "Семейный план включен",
            "final_price_default": "Финальная цена (обычный)",
            "final_price_family": "Финальная цена (семейный)",
            "order": "Порядок",
        },
        "promo_batches": {
            "id": "ID",
            "title": "Название",
            "notes": "Заметки",
            "created_at": "Создана",
            "created_by_id": "Создал",
        },
        "promo_codes": {
            "id": "ID",
            "name": "Имя",
            "code_hmac": "Код (HMAC)",
            "effects": "Эффекты",
            "batch_id": "Партия",
            "max_activations": "Макс. активаций",
            "per_user_limit": "На пользователя",
            "expires_at": "Истекает",
            "disabled": "Отключен",
            "created_at": "Создан",
        },
        "promo_usages": {
            "id": "ID",
            "promo_code_id": "Промокод",
            "user_id": "Пользователь",
            "used_at": "Дата использования",
            "context": "Контекст",
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
        "error_reports": {
            "event_id": "Event ID",
            "code": "Код ошибки",
            "type": "Тип ошибки",
            "message": "Сообщение",
            "route": "Маршрут",
            "href": "URL",
            "user_id": "Пользователь",
            "triage_severity": "Критичность",
            "triage_status": "Статус обработки",
            "triage_owner": "Ответственный",
            "triage_note": "Комментарий",
            "triage_due_at": "SLA дедлайн",
            "triage_updated_at": "Обновлен",
            "created_at": "Создан",
            "reported_at": "Время на клиенте",
        },
        "in_app_notifications": {
            "id": "ID",
            "title": "Заголовок",
            "body": "Текст уведомления",
            "start_at": "Показывать с",
            "end_at": "Показывать до",
            "max_per_user": "Макс. показов на пользователя",
            "max_per_session": "Макс. показов на сессию",
            "auto_hide_seconds": "Авто-скрытие (сек)",
            "is_active": "Активно",
            "created_at": "Создано",
            "updated_at": "Обновлено",
        },
        "notification_views": {
            "id": "ID",
            "user_id": "Пользователь",
            "notification_id": "Уведомление",
            "session_id": "Сессия",
            "viewed_at": "Просмотрено",
        },
    }

    for collection, fields in field_notes.items():
        for field, note in fields.items():
            meta_payload: Dict[str, Any] = {"note": note}
            translation = field_translations.get(collection, {}).get(field)
            if translation:
                meta_payload["translations"] = [{"language": "ru-RU", "translation": translation}]
            patch_field_meta(client, collection, field, meta_payload)

    # Error reports triage controls
    patch_field_meta(
        client,
        "error_reports",
        "triage_status",
        {
            "interface": "select-dropdown",
            "options": {
                "choices": [
                    {"text": "Новый", "value": "new"},
                    {"text": "В работе", "value": "in_progress"},
                    {"text": "Исправлен", "value": "resolved"},
                ]
            },
        },
    )
    patch_field_meta(
        client,
        "error_reports",
        "triage_severity",
        {
            "interface": "select-dropdown",
            "options": {
                "choices": [
                    {"text": "Низкая", "value": "low"},
                    {"text": "Средняя", "value": "medium"},
                    {"text": "Высокая", "value": "high"},
                    {"text": "Критичная", "value": "critical"},
                ]
            },
        },
    )
    patch_field_meta(client, "error_reports", "triage_note", {"interface": "input-multiline"})
    patch_field_meta(client, "error_reports", "triage_due_at", {"interface": "datetime"})
    patch_field_meta(client, "error_reports", "triage_updated_at", {"interface": "datetime", "readonly": True})
    patch_field_meta(client, "error_reports", "created_at", {"interface": "datetime", "readonly": True})
    patch_field_meta(client, "error_reports", "reported_at", {"interface": "datetime"})


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
        # Some Directus setups deny reading field metadata (403) while still allowing
        # field creation. In that case, we still attempt POST and rely on idempotent
        # handling below (409 duplicate is treated as success).
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
    ensure_field(
        "maintenance_mode",
        "boolean",
        {"default_value": False},
        {"interface": "boolean", "note": "Включить режим технических работ для клиентского приложения"},
    )
    ensure_field(
        "maintenance_message",
        "string",
        {"default_value": ""},
        {"interface": "input-multiline", "note": "Кастомный текст техработ (что происходит, сроки и т.д.)"},
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
        "maintenance_mode": False,
        "maintenance_message": "",
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
        ensure_permission(client, manager_policy_id, collection, "create")
    if viewer_policy_id:
        ensure_permission(client, viewer_policy_id, collection, "read")


def apply_error_reports_form_ux(client: DirectusClient) -> None:
    """
    Make error reports list/form practical for triage workflow.
    """
    if client.get("/collections/error_reports").status_code != 200:
        return

    widths = {
        "id": "quarter",
        "created_at": "half",
        "reported_at": "half",
        "triage_severity": "quarter",
        "triage_status": "quarter",
        "triage_due_at": "half",
        "triage_owner": "quarter",
        "type": "quarter",
        "code": "half",
        "user_id": "quarter",
        "route": "half",
        "message": "full",
        "triage_note": "full",
    }
    sort = {
        "created_at": 1,
        "reported_at": 2,
        "triage_severity": 3,
        "triage_status": 4,
        "triage_due_at": 5,
        "triage_owner": 6,
        "type": 7,
        "code": 8,
        "user_id": 9,
        "route": 10,
        "message": 11,
        "triage_note": 12,
    }

    for field, width in widths.items():
        meta: Dict[str, Any] = {"width": width, "hidden": False}
        if field in sort:
            meta["sort"] = sort[field]
        patch_field_meta(client, "error_reports", field, meta)

    patch_field_meta(client, "error_reports", "message", {"interface": "input-multiline"})
    patch_field_meta(client, "error_reports", "triage_note", {"interface": "input-multiline"})


def apply_users_form_ux(client: DirectusClient) -> None:
    """
    Improve the default item (detail) form for `users`:
    - better widths (more "app-like" editing)
    - readonly fields aligned with legacy FastAdmin behavior
    - predictable ordering for key ops fields
    """

    # Readonly by legacy admin (FastAdmin): keep identity/audit immutable.
    readonly_fields = {
        "id",
        "registration_date",
        "created_at",
        "activation_date",
        "referrals",
        "referral_bonus_days_total",
        "referral_first_payment_rewarded",
        "username",
        "full_name",
        "used_trial",
        "remnawave_uuid",
        "connected_at",
        "last_hwid_reset",
        "last_failed_message_at",
        "failed_message_count",
        "familyurl",
    }

    # Widths: keep the form compact and scannable on wide displays.
    widths = {
        "is_admin": "quarter",
        "id": "quarter",
        "username": "half",
        "full_name": "half",
        "email": "half",
        "language_code": "quarter",
        "utm": "half",
        "renew_id": "half",
        "created_at": "half",
        "registration_date": "half",
        "activation_date": "half",
        "connected_at": "half",
        "expired_at": "half",
        "balance": "quarter",
        "lte_gb_total": "quarter",
        "hwid_limit": "quarter",
        "prize_wheel_attempts": "quarter",
        "active_tariff": "half",
        "active_tariff_id": "half",
        "is_registered": "quarter",
        "is_subscribed": "quarter",
        "is_trial": "quarter",
        "used_trial": "quarter",
        "is_blocked": "quarter",
        "blocked_at": "half",
        "last_failed_message_at": "half",
        "failed_message_count": "quarter",
        "is_partner": "quarter",
        "custom_referral_percent": "quarter",
        "referred_by": "quarter",
        "referrals": "quarter",
        "referral_bonus_days_total": "quarter",
        "referral_first_payment_rewarded": "quarter",
        "referred_users_list": "full",
        "active_tariffs_list": "full",
        "promo_usages_list": "full",
        "notification_marks_list": "full",
        "family_devices_list": "full",
        "partner_withdrawals_list": "full",
        "partner_earnings_list": "full",
        "family_audit_logs_owner": "full",
        "family_members_owner_list": "full",
        "family_members_member_list": "full",
        "family_invites_list": "full",
        "remnawave_uuid": "half",
        "last_hwid_reset": "half",
        "familyurl": "half",
    }

    # NOTE:
    # In this production instance, grouping fields via `meta.group` to
    # presentation-divider aliases can hide fields in item view.
    # We keep dividers as visual separators by sort order, but keep fields ungrouped.

    # Sort order (best-effort): smaller number = higher on the form.
    sort = {
        "is_admin": 9,
        "id": 10,
        "username": 11,
        "full_name": 12,
        "email": 13,
        "language_code": 14,
        "created_at": 15,
        "registration_date": 16,
        "activation_date": 17,
        "connected_at": 18,
        "utm": 19,
        "renew_id": 20,
        "expired_at": 30,
        "active_tariff": 31,
        "active_tariff_id": 67,
        "is_registered": 32,
        "is_subscribed": 33,
        "is_trial": 34,
        "used_trial": 35,
        "balance": 40,
        "lte_gb_total": 41,
        "hwid_limit": 42,
        "prize_wheel_attempts": 43,
        "is_blocked": 50,
        "blocked_at": 51,
        "last_failed_message_at": 52,
        "failed_message_count": 53,
        "is_partner": 60,
        "custom_referral_percent": 61,
        "referred_by": 62,
        "referrals": 63,
        "referral_bonus_days_total": 64,
        "referral_first_payment_rewarded": 65,
        "referred_users_list": 68,
        "active_tariffs_list": 69,
        "promo_usages_list": 70,
        "notification_marks_list": 71,
        "family_devices_list": 72,
        "partner_withdrawals_list": 73,
        "partner_earnings_list": 74,
        "family_audit_logs_owner": 75,
        "family_members_owner_list": 76,
        "family_members_member_list": 77,
        "family_invites_list": 78,
        "remnawave_uuid": 80,
        "last_hwid_reset": 81,
        "familyurl": 82,
    }

    # Important: Directus may treat `PATCH /fields/...` with `meta` as replacement-like
    # for nested UI settings in some deployments. Preserve existing UI keys so o2m/alias
    # blocks (including Family) don't lose their interface and appear as "empty sections".
    preserve_meta_keys = ("interface", "options", "display", "special", "note", "translations")

    def get_existing_meta(field: str) -> Dict[str, Any]:
        resp = client.get(f"/fields/users/{field}", params={"fields": "meta"})
        if resp.status_code in (403, 404):
            return {}
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        meta = data.get("meta") or {}
        return meta if isinstance(meta, dict) else {}

    for field, width in widths.items():
        existing_meta = get_existing_meta(field)
        # Force field visibility in item form: legacy configs could leave business
        # fields hidden, which makes section dividers look "empty".
        meta: Dict[str, Any] = {"width": width, "hidden": False}
        for key in preserve_meta_keys:
            if key in existing_meta and existing_meta[key] is not None:
                meta[key] = existing_meta[key]
        if field in readonly_fields:
            meta["readonly"] = True
        if field in sort:
            meta["sort"] = sort[field]
        meta["group"] = None
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

    # Keep divider sort values between field blocks sorted in apply_users_form_ux().
    # Legacy set is used intentionally: users already know these section names.
    ensure_divider("ui_divider_overview", "Основное", "person", 1)
    ensure_divider("ui_divider_subscription", "Подписка", "event", 29)
    ensure_divider("ui_divider_limits", "Лимиты", "tune", 39)
    ensure_divider("ui_divider_status", "Статус", "shield", 49)
    ensure_divider("ui_divider_partner", "Партнерка", "groups", 59)
    ensure_divider("ui_divider_ops", "Операции", "build", 66)
    ensure_divider("ui_divider_family", "Семья", "family_restroom", 75)
    ensure_divider("ui_divider_tech", "Техника", "settings", 79)

    # Hide deprecated divider variants to avoid duplicate/empty sections.
    for legacy_divider in ("ui_divider_profile", "ui_divider_finance", "ui_divider_relations"):
        patch_field_meta(client, "users", legacy_divider, {"hidden": True, "sort": 9990})

    # Improve interfaces for key fields (best-effort).
    # If a field doesn't exist in the current instance, patch_field_meta will safely skip.
    # IMPORTANT: Explicit interfaces are required in some Directus setups; fields with
    # interface=None can disappear from item form even if not hidden.
    explicit_interfaces: Dict[str, Dict[str, Any]] = {
        # Overview
        "id": {"interface": "input"},
        "username": {"interface": "input"},
        "full_name": {"interface": "input"},
        "email": {"interface": "input"},
        "language_code": {"interface": "input"},
        "utm": {"interface": "input"},
        "renew_id": {"interface": "input"},
        # Limits / numeric
        "balance": {"interface": "input"},
        "lte_gb_total": {"interface": "input"},
        "hwid_limit": {"interface": "input"},
        "prize_wheel_attempts": {"interface": "input"},
        "failed_message_count": {"interface": "input"},
        "referrals": {"interface": "input"},
        "referral_bonus_days_total": {"interface": "input"},
        # Tech
        "remnawave_uuid": {"interface": "input"},
        "familyurl": {"interface": "input"},
        # Booleans
        "referral_first_payment_rewarded": {"interface": "toggle", "options": {"label": "Награда за первый платеж"}},
    }
    for field, meta_patch in explicit_interfaces.items():
        patch_field_meta(client, "users", field, meta_patch)

    patch_field_meta(client, "users", "is_blocked", {"interface": "toggle", "options": {"label": "Заблокирован"}})
    patch_field_meta(client, "users", "is_registered", {"interface": "toggle", "options": {"label": "Зарегистрирован"}})
    patch_field_meta(client, "users", "is_subscribed", {"interface": "toggle", "options": {"label": "Подписан"}})
    patch_field_meta(client, "users", "is_trial", {"interface": "toggle", "options": {"label": "Триал"}})
    patch_field_meta(client, "users", "used_trial", {"interface": "toggle", "options": {"label": "Триал использован"}})
    patch_field_meta(client, "users", "is_partner", {"interface": "toggle", "options": {"label": "Партнер"}})
    patch_field_meta(
        client,
        "users",
        "referred_by",
        {
            "interface": "id-link-editor",
            "options": {"collection": "users", "openInNewTab": False},
            "sort": 62,
            "note": "Чей реферал: можно быстро перейти к карточке связанного пользователя.",
        },
    )
    patch_field_meta(
        client,
        "users",
        "active_tariff",
        {
            "interface": "id-link-editor",
            "options": {"collection": "active_tariffs", "openInNewTab": False},
        },
    )
    patch_field_meta(
        client,
        "users",
        "active_tariff_id",
        {
            "interface": "id-link-editor",
            "options": {"collection": "active_tariffs", "openInNewTab": False},
            "sort": 67,
        },
    )
    patch_field_meta(
        client,
        "users",
        "is_admin",
        {
            "interface": "toggle",
            "options": {"label": "Администратор"},
            "width": "quarter",
            "sort": 9,
            "hidden": False,
        },
    )
    patch_field_meta(client, "users", "expired_at", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "created_at", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "registration_date", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "activation_date", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "connected_at", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "blocked_at", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "last_hwid_reset", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "last_failed_message_at", {"interface": "datetime", "options": {"use24": True, "includeSeconds": False}})
    patch_field_meta(client, "users", "custom_referral_percent", {"interface": "slider", "options": {"min": 0, "max": 100, "step": 1, "alwaysShowValue": True}})


def apply_tariffs_form_ux(client: DirectusClient) -> None:
    """
    Make tariffs form explicit and editable by real card semantics:
    - operators edit final prices first
    - system parameters (base/multiplier) stay visible as technical fields
    """
    if client.get("/collections/tariffs").status_code != 200:
        return

    widths = {
        "id": "quarter",
        "name": "half",
        "months": "quarter",
        "order": "quarter",
        "is_active": "quarter",
        "family_plan_enabled": "quarter",
        "final_price_default": "half",
        "final_price_family": "half",
        "devices_limit_default": "quarter",
        "devices_limit_family": "quarter",
        "base_price": "half",
        "progressive_multiplier": "half",
        "lte_enabled": "quarter",
        "lte_price_per_gb": "quarter",
    }

    sort = {
        "id": 10,
        "name": 11,
        "months": 12,
        "order": 13,
        "is_active": 14,
        "family_plan_enabled": 15,
        "final_price_default": 20,
        "final_price_family": 21,
        "devices_limit_default": 30,
        "devices_limit_family": 31,
        "base_price": 40,
        "progressive_multiplier": 41,
        "lte_enabled": 50,
        "lte_price_per_gb": 51,
    }

    for field, width in widths.items():
        meta: Dict[str, Any] = {"width": width, "hidden": False, "group": None}
        if field in sort:
            meta["sort"] = sort[field]
        if field == "id":
            meta["readonly"] = True
        patch_field_meta(client, "tariffs", field, meta)

    patch_field_meta(client, "tariffs", "is_active", {"interface": "toggle", "options": {"label": "Активен"}})
    patch_field_meta(client, "tariffs", "family_plan_enabled", {"interface": "toggle", "options": {"label": "Семейный план"}})
    patch_field_meta(client, "tariffs", "lte_enabled", {"interface": "toggle", "options": {"label": "LTE включен"}})
    patch_field_meta(client, "tariffs", "name", {"interface": "input"})
    patch_field_meta(client, "tariffs", "months", {"interface": "input"})
    patch_field_meta(client, "tariffs", "order", {"interface": "input"})
    patch_field_meta(client, "tariffs", "final_price_default", {"interface": "input"})
    patch_field_meta(client, "tariffs", "final_price_family", {"interface": "input"})
    patch_field_meta(client, "tariffs", "devices_limit_default", {"interface": "input"})
    patch_field_meta(client, "tariffs", "devices_limit_family", {"interface": "input"})
    patch_field_meta(client, "tariffs", "base_price", {"interface": "input"})
    patch_field_meta(client, "tariffs", "progressive_multiplier", {"interface": "input"})
    patch_field_meta(client, "tariffs", "lte_price_per_gb", {"interface": "input"})


def ensure_tariffs_presentation_dividers(client: DirectusClient) -> None:
    """
    Add clear visual structure and family logic explanation for tariffs form.
    """
    if client.get("/collections/tariffs").status_code != 200:
        return

    def ensure_alias_field(field: str, meta: Dict[str, Any]) -> None:
        resp = client.patch("/fields/tariffs/" + field, json={"meta": meta})
        if resp.status_code == 404:
            created = client.post(
                "/fields/tariffs",
                json={
                    "field": field,
                    "type": "alias",
                    "schema": None,
                    "meta": meta,
                },
            )
            if created.status_code in (401, 403, 409):
                return
            created.raise_for_status()
            return
        if resp.status_code in (401, 403):
            return
        resp.raise_for_status()

    ensure_alias_field(
        "ui_tariff_divider_core",
        {
            "interface": "presentation-divider",
            "special": ["alias", "no-data"],
            "options": {"title": "Карточка тарифа", "icon": "view_compact"},
            "width": "full",
            "sort": 1,
        },
    )
    ensure_alias_field(
        "ui_tariff_divider_prices",
        {
            "interface": "presentation-divider",
            "special": ["alias", "no-data"],
            "options": {"title": "Финальные цены карточек", "icon": "sell"},
            "width": "full",
            "sort": 19,
        },
    )
    ensure_alias_field(
        "ui_tariff_prices_notice",
        {
            "interface": "presentation-notice",
            "special": ["alias", "no-data"],
            "options": {
                "color": "info",
                "icon": "tips_and_updates",
                "text": (
                    "Меняйте в первую очередь финальные цены карточек. "
                    "Служебные параметры base_price и progressive_multiplier "
                    "автоматически пересчитываются backend-логикой."
                ),
            },
            "width": "full",
            "sort": 22,
        },
    )
    ensure_alias_field(
        "ui_tariff_divider_devices",
        {
            "interface": "presentation-divider",
            "special": ["alias", "no-data"],
            "options": {"title": "Лимиты устройств и family-логика", "icon": "devices"},
            "width": "full",
            "sort": 29,
        },
    )
    ensure_alias_field(
        "ui_tariff_family_notice",
        {
            "interface": "presentation-notice",
            "special": ["alias", "no-data"],
            "options": {
                "color": "normal",
                "icon": "family_restroom",
                "text": (
                    "Обычная карточка использует devices_limit_default. "
                    "Семейная карточка показывается только при family_plan_enabled=true "
                    "и devices_limit_family > devices_limit_default."
                ),
            },
            "width": "full",
            "sort": 32,
        },
    )
    ensure_alias_field(
        "ui_tariff_divider_formula",
        {
            "interface": "presentation-divider",
            "special": ["alias", "no-data"],
            "options": {"title": "Служебные параметры расчета", "icon": "functions"},
            "width": "full",
            "sort": 39,
        },
    )
    ensure_alias_field(
        "ui_tariff_formula_notice",
        {
            "interface": "presentation-notice",
            "special": ["alias", "no-data"],
            "options": {
                "color": "warning",
                "icon": "calculate",
                "text": (
                    "base_price/progressive_multiplier хранятся для обратной совместимости "
                    "и платежных snapshot-ов. Обычно вручную их менять не нужно."
                ),
            },
            "width": "full",
            "sort": 42,
        },
    )
    ensure_alias_field(
        "ui_tariff_divider_lte",
        {
            "interface": "presentation-divider",
            "special": ["alias", "no-data"],
            "options": {"title": "LTE", "icon": "network_cell"},
            "width": "full",
            "sort": 49,
        },
    )


def apply_users_luxury_ux(client: DirectusClient) -> None:
    """
    Add a safe premium layer for users item-view without changing base layout:
    - keep previous iteration field order intact (no sort/width mutations)
    - add optional visual separators only
    - enrich notes/templates for operational blocks
    """

    if client.get("/collections/users").status_code != 200:
        return

    def ensure_divider(field: str, title: str, icon: str, sort: int) -> None:
        meta = {
            "interface": "presentation-divider",
            "options": {"title": title, "icon": icon},
            "special": ["alias", "no-data"],
            "width": "full",
            "sort": sort,
        }
        resp = client.patch("/fields/users/" + field, json={"meta": meta})
        if resp.status_code == 404:
            created = client.post(
                "/fields/users",
                json={
                    "field": field,
                    "type": "alias",
                    "schema": None,
                    "meta": meta,
                },
            )
            if created.status_code in (401, 403, 409):
                return
            created.raise_for_status()
            return
        if resp.status_code in (401, 403):
            return
        resp.raise_for_status()

    def ensure_alias_field(field: str, meta: Dict[str, Any]) -> None:
        resp = client.patch("/fields/users/" + field, json={"meta": meta})
        if resp.status_code == 404:
            created = client.post(
                "/fields/users",
                json={
                    "field": field,
                    "type": "alias",
                    "schema": None,
                    "meta": meta,
                },
            )
            if created.status_code in (401, 403, 409):
                return
            created.raise_for_status()
            return
        if resp.status_code in (401, 403):
            return
        resp.raise_for_status()

    # Keep optional luxury section at the very bottom so base sections are not
    # affected. It is purely additive and does not re-order existing fields.
    ensure_divider("ui_divider_kpi", "KPI и быстрые действия", "insights", 9900)
    patch_field_meta(client, "users", "ui_divider_quick_actions", {"hidden": True, "sort": 9992})
    ensure_alias_field(
        "ui_kpi_notice",
        {
            "interface": "presentation-notice",
            "special": ["alias", "no-data"],
            "options": {
                "color": "info",
                "icon": "insights",
                "text": (
                    "Единый блок KPI/действий: быстрый доступ к метрикам и переходам "
                    "строго по текущему пользователю."
                ),
            },
            "width": "full",
            "sort": 9901,
        },
    )
    ensure_alias_field(
        "ui_filter_indicator",
        {
            "interface": "presentation-notice",
            "special": ["alias", "no-data"],
            "options": {
                "color": "normal",
                "icon": "filter_alt",
                "text": "Фильтр активен: переходы ниже ведут к встроенным спискам карточки текущего пользователя.",
            },
            "width": "half",
            "sort": 9902,
        },
    )
    ensure_alias_field(
        "ui_kpi_links",
        {
            "interface": "presentation-links",
            "special": ["alias", "no-data"],
            "options": {
                "links": [
                    {
                        "label": "История тарифов (в карточке)",
                        "icon": "subscriptions",
                        "type": "primary",
                        "actionType": "url",
                        "url": "/content/users/{{id}}#active_tariffs_list",
                    },
                    {
                        "label": "История промокодов (в карточке)",
                        "icon": "confirmation_number",
                        "type": "info",
                        "actionType": "url",
                        "url": "/content/users/{{id}}#promo_usages_list",
                    },
                    {
                        "label": "История логов уведомлений (в карточке)",
                        "icon": "notifications",
                        "type": "normal",
                        "actionType": "url",
                        "url": "/content/users/{{id}}#notification_marks_list",
                    },
                    {
                        "label": "История семейного аудита (в карточке)",
                        "icon": "fact_check",
                        "type": "normal",
                        "actionType": "url",
                        "url": "/content/users/{{id}}#family_audit_logs_owner",
                    },
                ]
            },
            "width": "full",
            "sort": 9903,
        },
    )
    ensure_alias_field(
        "ui_quick_actions_notice",
        {
            "interface": "presentation-notice",
            "special": ["alias", "no-data"],
            "options": {
                "color": "success",
                "icon": "bolt",
                "text": (
                    "Каждый переход открывает либо конкретную связанную сущность, "
                    "либо встроенные списки в карточке пользователя (уже отфильтрованные по нему)."
                ),
            },
            "width": "full",
            "sort": 9904,
        },
    )
    ensure_alias_field(
        "ui_quick_actions_links",
        {
            "interface": "presentation-links",
            "special": ["alias", "no-data"],
            "options": {
                "links": [
                    {
                        "label": "Открыть карточку реферера",
                        "icon": "person_search",
                        "type": "primary",
                        "actionType": "url",
                        "url": "/content/users/{{referred_by}}",
                    },
                    {
                        "label": "Открыть активный тариф",
                        "icon": "local_offer",
                        "type": "primary",
                        "actionType": "url",
                        "url": "/content/active_tariffs/{{active_tariff_id}}",
                    },
                    {
                        "label": "Открыть список рефералов",
                        "icon": "groups",
                        "type": "normal",
                        "actionType": "url",
                        "url": "/content/users/{{id}}#referred_users_list",
                    },
                ]
            },
            "width": "full",
            "sort": 9905,
        },
    )

    # Premium templates for embedded logs.
    patch_field_meta(
        client,
        "users",
        "active_tariffs_list",
        {
            "interface": "list-o2m",
            "options": {"template": "{{id}} • {{name}} • {{start_date}} → {{end_date}} • LTE {{lte_gb_used}}/{{lte_gb_total}}"},
            "note": "История тарифов пользователя.",
        },
    )
    patch_field_meta(
        client,
        "users",
        "promo_usages_list",
        {
            "interface": "list-o2m",
            "options": {"template": "{{id}} • {{used_at}} • {{promo_code_id}}"},
            "note": "Какие промокоды и когда были применены.",
        },
    )
    patch_field_meta(
        client,
        "users",
        "notification_marks_list",
        {
            "interface": "list-o2m",
            "options": {"template": "{{id}} • {{type}} • {{sent_at}}"},
            "note": "Логи коммуникаций/уведомлений по пользователю.",
        },
    )
    patch_field_meta(
        client,
        "users",
        "family_audit_logs_owner",
        {
            "interface": "list-o2m",
            "options": {"template": "{{id}} • {{action}} • {{created_at}}"},
            "note": "Аудит действий в семейных сценариях.",
        },
    )
    patch_field_meta(
        client,
        "users",
        "family_members_owner_list",
        {
            "interface": "list-o2m",
            "options": {"template": "{{id}} • member {{member_id}} • {{status}} • {{allocated_devices}} devices"},
            "note": "Участники семьи, где пользователь выступает владельцем.",
        },
    )
    patch_field_meta(
        client,
        "users",
        "family_members_member_list",
        {
            "interface": "list-o2m",
            "options": {"template": "{{id}} • owner {{owner_id}} • {{status}} • {{allocated_devices}} devices"},
            "note": "Членство в семье, где пользователь выступает участником.",
        },
    )
    patch_field_meta(
        client,
        "users",
        "family_invites_list",
        {
            "interface": "list-o2m",
            "options": {"template": "{{id}} • {{used_count}}/{{max_uses}} • {{expires_at}}"},
            "note": "Инвайты семьи, созданные пользователем.",
        },
    )


def ensure_users_relations_ux(client: DirectusClient) -> None:
    """
    Turn users card into a true "workspace":
    - clickable m2o for referred_by
    - embedded o2m blocks for key activity/log collections
    """

    if client.get("/collections/users").status_code != 200:
        return

    def get_relation_item(many_collection: str, many_field: str) -> Optional[Dict[str, Any]]:
        # Prefer the dedicated /relations endpoint. In some Directus setups
        # /items/directus_relations is forbidden even for admin API users.
        resp = client.get(f"/relations/{many_collection}/{many_field}")
        if resp.status_code in (401, 403, 404):
            return None
        resp.raise_for_status()
        row = resp.json().get("data")
        if not isinstance(row, dict):
            return None
        return row

    def ensure_relation(
        *,
        many_collection: str,
        many_field: str,
        one_collection: str,
        one_field: str,
        one_deselect_action: str = "nullify",
        create_if_missing: bool = False,
    ) -> bool:
        relation = get_relation_item(many_collection, many_field)
        if relation:
            payload = {
                "collection": many_collection,
                "field": many_field,
                "related_collection": one_collection,
                "meta": {
                    "one_collection": one_collection,
                    "one_field": one_field,
                    "one_deselect_action": one_deselect_action,
                },
            }
            resp = client.patch(
                f"/relations/{many_collection}/{many_field}",
                json=payload,
            )
            if resp.status_code in (400, 401, 403, 404):
                return False
            if not resp.ok:
                return False
            return True

        if not create_if_missing:
            return False

        created = client.post(
            "/relations",
            json={
                "collection": many_collection,
                "field": many_field,
                "related_collection": one_collection,
                "meta": {
                    "one_collection": one_collection,
                    "one_field": one_field,
                    "one_deselect_action": one_deselect_action,
                },
            },
        )
        if created.status_code in (400, 401, 403, 404):
            return False
        # 409 => already exists / race, consider it success.
        if created.status_code == 409:
            return True
        if not created.ok:
            return False
        return True

    def ensure_alias_o2m_field(alias_field: str, title: str, sort: int, template: str = "{{id}}") -> None:
        meta = {
            "interface": "list-o2m",
            "options": {"template": template},
            "display": "related-values",
            # Critical: in some Directus instances newly created alias fields may default to hidden.
            # Force explicit visibility to avoid "empty section" regressions in item form.
            "hidden": False,
            "width": "full",
            "sort": sort,
            "group": None,
            "note": title,
            "translations": [{"language": "ru-RU", "translation": title}],
        }
        resp = client.patch(
            "/fields/users/" + alias_field,
            json={"meta": meta},
        )
        if resp.status_code == 404:
            created = client.post(
                "/fields/users",
                json={
                    "field": alias_field,
                    "type": "alias",
                    "schema": None,
                    "meta": meta,
                },
            )
            if created.status_code in (401, 403):
                return
            if created.status_code == 409:
                return
            # Some Directus versions return 400 "already exists" for race conditions.
            if created.status_code == 400:
                try:
                    body = created.json()
                except Exception:  # pragma: no cover
                    body = {}
                msg = str(body).lower()
                if "already exists" in msg:
                    return
            created.raise_for_status()
            return
        if resp.status_code in (401, 403):
            return
        resp.raise_for_status()

    # Self-reference for "Чей реферал": keep reverse o2m list visible and clickable.
    # Create alias first, then patch relation metadata. Some Directus instances reject
    # one_field updates when the target alias isn't created yet.
    ensure_alias_o2m_field("referred_users_list", "Рефералы пользователя", 68, "{{id}} — {{username}} — {{full_name}}")
    referred_rel_ok = ensure_relation(
        many_collection="users",
        many_field="referred_by",
        one_collection="users",
        one_field="referred_users_list",
        one_deselect_action="nullify",
        create_if_missing=True,
    )
    if referred_rel_ok:
        patch_field_meta(
            client,
            "users",
            "referred_by",
            {
                "interface": "select-dropdown-m2o",
                "display": "related-values",
                "note": "Чей реферал: можно открыть и сразу перейти в карточку родителя.",
                "readonly": False,
                "sort": 62,
            },
        )
        ensure_alias_o2m_field("referred_users_list", "Рефералы пользователя", 68, "{{id}} — {{username}} — {{full_name}}")
    elif client.get("/fields/users/referred_users_list").status_code == 200:
        ensure_alias_o2m_field("referred_users_list", "Рефералы пользователя", 68, "{{id}} — {{username}} — {{full_name}}")

    # Existing business relations -> embedded logs/history inside user card.
    relation_specs = [
        ("active_tariffs", "user_id", "active_tariffs_list", "История активных тарифов", 69, "{{id}} — {{name}} — LTE {{lte_gb_used}}/{{lte_gb_total}}"),
        ("promo_usages", "user_id", "promo_usages_list", "Использование промокодов", 70, "{{id}} — {{used_at}}"),
        ("notification_marks", "user_id", "notification_marks_list", "Логи уведомлений", 71, "{{id}} — {{type}} — {{sent_at}}"),
        ("family_devices", "user_id", "family_devices_list", "Устройства семьи", 72, "{{id}} — {{device_name}}"),
        ("partner_withdrawals", "owner_id", "partner_withdrawals_list", "Выводы партнера", 73, "{{id}} — {{status}} — {{amount}}"),
        ("partner_earnings", "partner_id", "partner_earnings_list", "Начисления партнера", 74, "{{id}} — {{amount}} — {{created_at}}"),
        ("family_audit_logs", "owner_id", "family_audit_logs_owner", "Аудит семьи", 75, "{{id}} — {{action}} — {{created_at}}"),
        ("family_members", "owner_id", "family_members_owner_list", "Участники семьи (owner)", 76, "{{id}} — member {{member_id}} — {{status}} — {{allocated_devices}}"),
        ("family_members", "member_id", "family_members_member_list", "Членство в семье (member)", 77, "{{id}} — owner {{owner_id}} — {{status}} — {{allocated_devices}}"),
        ("family_invites", "owner_id", "family_invites_list", "Инвайты семьи", 78, "{{id}} — {{used_count}}/{{max_uses}} — {{expires_at}}"),
    ]
    for many_collection, many_field, one_field, title, sort, template in relation_specs:
        # Make the o2m block visible first; relation metadata can be patched after.
        ensure_alias_o2m_field(one_field, title, sort, template)
        rel_ok = ensure_relation(
            many_collection=many_collection,
            many_field=many_field,
            one_collection="users",
            one_field=one_field,
            one_deselect_action="nullify",
            create_if_missing=True,
        )
        # If relation metadata update is blocked in this environment but alias field
        # already exists, still enforce o2m interface for better in-form UX.
        alias_exists = client.get(f"/fields/users/{one_field}").status_code == 200
        if rel_ok or alias_exists:
            ensure_alias_o2m_field(one_field, title, sort, template)


def ensure_users_family_section_ux(client: DirectusClient) -> None:
    """
    Final self-heal for users -> Family section.

    Why:
    - some Directus environments can partially apply relation metadata
      (or lose one_field/interface after upgrades/imports);
    - this phase re-applies family o2m aliases and relation links to avoid
      empty "Семья" section regressions.
    """
    if client.get("/collections/users").status_code != 200:
        return

    field_specs: list[tuple[str, str, int, str]] = [
        (
            "family_audit_logs_owner",
            "Аудит семьи",
            75,
            "{{id}} — {{action}} — {{created_at}}",
        ),
        (
            "family_members_owner_list",
            "Участники семьи (owner)",
            76,
            "{{id}} — member {{member_id}} — {{status}} — {{allocated_devices}}",
        ),
        (
            "family_members_member_list",
            "Членство в семье (member)",
            77,
            "{{id}} — owner {{owner_id}} — {{status}} — {{allocated_devices}}",
        ),
        (
            "family_invites_list",
            "Инвайты семьи",
            78,
            "{{id}} — {{used_count}}/{{max_uses}} — {{expires_at}}",
        ),
    ]

    relation_specs: list[tuple[str, str, str]] = [
        ("family_audit_logs", "owner_id", "family_audit_logs_owner"),
        ("family_members", "owner_id", "family_members_owner_list"),
        ("family_members", "member_id", "family_members_member_list"),
        ("family_invites", "owner_id", "family_invites_list"),
    ]

    for many_collection, many_field, one_field in relation_specs:
        resp = client.patch(
            f"/relations/{many_collection}/{many_field}",
            json={
                "collection": many_collection,
                "field": many_field,
                "related_collection": "users",
                "meta": {
                    "one_collection": "users",
                    "one_field": one_field,
                    "one_deselect_action": "nullify",
                },
            },
        )
        # Best-effort: missing relation/forbidden should not break whole setup.
        if resp.status_code in (400, 401, 403, 404):
            continue
        if resp.status_code not in (502, 503, 504):
            resp.raise_for_status()

    for field, title, sort, template in field_specs:
        # Create alias field if missing.
        create_resp = client.post(
            "/fields/users",
            json={
                "field": field,
                "type": "alias",
                "schema": None,
                "meta": {
                    "interface": "list-o2m",
                    "display": "related-values",
                    "options": {"template": template},
                    "width": "full",
                    "sort": sort,
                    "hidden": False,
                    "group": None,
                    "note": title,
                    "translations": [{"language": "ru-RU", "translation": title}],
                },
            },
        )
        if create_resp.status_code not in (200, 201, 204, 400, 401, 403, 404, 409):
            create_resp.raise_for_status()

        patch_field_meta(
            client,
            "users",
            field,
            {
                "interface": "list-o2m",
                "display": "related-values",
                "options": {"template": template},
                "width": "full",
                "sort": sort,
                "hidden": False,
                "group": None,
                "note": title,
                "translations": [{"language": "ru-RU", "translation": title}],
            },
        )


def ensure_users_family_workspace_aliases(client: DirectusClient) -> None:
    """
    Fallback workspace inside users item form.

    Even if relation widgets are hidden by Directus internals, operators still
    get visible family controls and quick links.
    """
    if client.get("/collections/users").status_code != 200:
        return

    def ensure_alias_field(field: str, meta: Dict[str, Any]) -> None:
        resp = client.patch("/fields/users/" + field, json={"meta": meta})
        if resp.status_code == 404:
            created = client.post(
                "/fields/users",
                json={
                    "field": field,
                    "type": "alias",
                    "schema": None,
                    "meta": meta,
                },
            )
            if created.status_code in (401, 403, 409):
                return
            created.raise_for_status()
            return
        if resp.status_code in (401, 403):
            return
        resp.raise_for_status()

    ensure_alias_field(
        "family_workspace_notice",
        {
            "interface": "presentation-notice",
            "special": ["alias", "no-data"],
            "options": {
                "color": "info",
                "icon": "family_restroom",
                "text": (
                    "Блок Семья: быстрый доступ к family-операциям. "
                    "Если relation-виджеты временно не отрисовались, "
                    "используйте ссылки ниже."
                ),
            },
            "width": "full",
            "sort": 76,
            "hidden": False,
            "group": None,
            "translations": [{"language": "ru-RU", "translation": "Семья: быстрый доступ"}],
        },
    )

    ensure_alias_field(
        "family_workspace_links",
        {
            "interface": "presentation-links",
            "special": ["alias", "no-data"],
            "options": {
                "links": [
                    {
                        "label": "Участники семьи (owner = текущий user)",
                        "icon": "groups",
                        "type": "primary",
                        "actionType": "url",
                        "url": "/content/family_members?filter[owner_id][_eq]={{id}}",
                    },
                    {
                        "label": "Членство пользователя в семье (member = текущий user)",
                        "icon": "person_search",
                        "type": "info",
                        "actionType": "url",
                        "url": "/content/family_members?filter[member_id][_eq]={{id}}",
                    },
                    {
                        "label": "Инвайты семьи (owner = текущий user)",
                        "icon": "mail",
                        "type": "normal",
                        "actionType": "url",
                        "url": "/content/family_invites?filter[owner_id][_eq]={{id}}",
                    },
                    {
                        "label": "Аудит семьи (owner = текущий user)",
                        "icon": "fact_check",
                        "type": "normal",
                        "actionType": "url",
                        "url": "/content/family_audit_logs?filter[owner_id][_eq]={{id}}",
                    },
                ]
            },
            "width": "full",
            "sort": 77,
            "hidden": False,
            "group": None,
            "translations": [{"language": "ru-RU", "translation": "Семья: ссылки"}],
        },
    )


def verify_users_family_section_visibility(client: DirectusClient) -> None:
    """
    Safety check: family section fields must stay visible and relation-based.
    """
    expected_interfaces = {
        "family_workspace_notice": "presentation-notice",
        "family_workspace_links": "presentation-links",
        "family_audit_logs_owner": "list-o2m",
        "family_members_owner_list": "list-o2m",
        "family_members_member_list": "list-o2m",
        "family_invites_list": "list-o2m",
    }
    for field, expected_interface in expected_interfaces.items():
        resp = client.get(f"/fields/users/{field}", params={"fields": "field,meta.interface,meta.hidden,meta.sort"})
        if resp.status_code in (401, 403, 404):
            print(f"WARN: users field {field} not readable (status={resp.status_code})")
            continue
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        meta = data.get("meta") or {}
        if meta.get("interface") != expected_interface:
            print(
                f"WARN: users field {field} interface is {meta.get('interface')!r}, "
                f"expected {expected_interface!r}"
            )
        if bool(meta.get("hidden", False)):
            print(f"WARN: users field {field} is hidden")

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
    admin_policy_id = get_policy_id_by_name(client, "Administrator") or get_primary_policy_id_for_role(client, admin_role_id)
    if admin_policy_id:
        for collection in (
            "directus_policies",
            "directus_roles",
            "directus_permissions",
            "directus_access",
            "directus_relations",
            "directus_fields",
            "directus_collections",
        ):
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

    # Some Directus setups can still fail item view navigation without explicit
    # read permission rows on business collections (even for admin-like roles).
    # Keep this explicit to avoid "list works, card doesn't open" regressions.
    admin_policy_id = get_policy_id_by_name(client, "Administrator") or get_primary_policy_id_for_role(client, admin_role_id)

    existing_collections = set(list_collections(client))

    manager_rw = {
        "users",
        "active_tariffs",
        "tariffs",
        "family_members",
        "family_invites",
        "family_devices",
        "family_audit_logs",
        "promo_batches",
        "promo_codes",
        "promo_usages",
        "prize_wheel_config",
        "prize_wheel_history",
        "processed_payments",
        "in_app_notifications",
        # Partners (optional)
        "partner_withdrawals",
    }
    manager_update_only = {
        "error_reports",
    }
    manager_ro = {
        "connections",
        "error_reports",
        "notification_views",
    }
    viewer_ro = {
        "users",
        "active_tariffs",
        "tariffs",
        "family_members",
        "family_invites",
        "family_devices",
        "family_audit_logs",
        "connections",
        "processed_payments",
        "error_reports",
        "in_app_notifications",
        "notification_views",
    }
    admin_rw = {
        "users",
        "active_tariffs",
        "tariffs",
        "family_members",
        "family_invites",
        "family_devices",
        "family_audit_logs",
        "promo_batches",
        "promo_codes",
        "promo_usages",
        "prize_wheel_config",
        "prize_wheel_history",
        "connections",
        "processed_payments",
        "in_app_notifications",
        "notification_views",
        # Partners (optional)
        "partner_withdrawals",
        "partner_qr_codes",
        "partner_earnings",
        "error_reports",
    }

    # Narrow to collections that actually exist in the current instance.
    manager_rw = {c for c in manager_rw if c in existing_collections}
    manager_update_only = {c for c in manager_update_only if c in existing_collections}
    manager_ro = {c for c in manager_ro if c in existing_collections}
    viewer_ro = {c for c in viewer_ro if c in existing_collections}
    admin_rw = {c for c in admin_rw if c in existing_collections}

    if admin_policy_id:
        for collection in sorted(admin_rw):
            for action in ("read", "create", "update", "delete"):
                ensure_permission(client, admin_policy_id, collection, action)

    for collection in sorted(manager_rw):
        for action in ("read", "create", "update", "delete"):
            ensure_permission(client, manager_policy_id, collection, action)
    for collection in sorted(manager_ro):
        ensure_permission(client, manager_policy_id, collection, "read")
    for collection in sorted(manager_update_only):
        ensure_permission(client, manager_policy_id, collection, "update")

    for collection in sorted(viewer_ro):
        ensure_permission(client, viewer_policy_id, collection, "read")

    # Dashboard settings (singleton): allow Manager to read/update/create, Viewer read-only.
    if client.get("/collections/tvpn_admin_settings").status_code == 200:
        ensure_permission(client, manager_policy_id, "tvpn_admin_settings", "read")
        ensure_permission(client, manager_policy_id, "tvpn_admin_settings", "update")
        ensure_permission(client, manager_policy_id, "tvpn_admin_settings", "create")
        ensure_permission(client, viewer_policy_id, "tvpn_admin_settings", "read")

    # Critical for Data Studio form rendering:
    # without explicit read access to schema metadata collections, item views
    # can load as blank sections for non-admin roles.
    system_schema_collections = {
        "directus_collections",
        "directus_fields",
        "directus_relations",
        "directus_presets",
    }
    for collection in sorted(system_schema_collections):
        ensure_permission(client, manager_policy_id, collection, "read")
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


def ensure_role_presets_legacy(client: DirectusClient) -> None:
    # Presets/bookmarks define the UX of listing pages (fields, widths, filters, sorts).
    role_map = get_role_map(client)
    admin_role = role_map.get("Administrator")
    manager = role_map.get("Manager")
    viewer = role_map.get("Viewer")
    if not admin_role and not manager and not viewer:
        return

    existing_resp = client.get(
        "/presets",
        params={
            "limit": 400,
            "fields": "id,role,user,collection,bookmark,layout,layout_query",
        },
    )
    existing_resp.raise_for_status()
    existing = existing_resp.json().get("data", []) or []

    def ensure_tabular_fields_include_pk(preset: Dict[str, Any], *, pk_field: str = "id") -> Optional[Dict[str, Any]]:
        """
        Directus list UX depends on items having a primary key.
        If tabular `fields` omit the PK, item navigation on row-click may silently stop working.
        """
        layout_query = preset.get("layout_query")
        if not isinstance(layout_query, dict):
            return {"tabular": {"fields": [pk_field]}}
        tabular = layout_query.get("tabular")
        if not isinstance(tabular, dict):
            return {"tabular": {"fields": [pk_field]}}
        fields = tabular.get("fields")
        if not isinstance(fields, list):
            new_tabular = {**tabular, "fields": [pk_field]}
            return {**layout_query, "tabular": new_tabular}
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
            layout_query = {}
        cards = layout_query.get("cards")
        if not isinstance(cards, dict):
            cards = {}
        fields = cards.get("fields")
        if not isinstance(fields, list):
            fields = []

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
                "bookmark": "Тарифы: карточки витрины",
                "user": None,
                "role": rid,
                "collection": "tariffs",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": [
                            "id",
                            "order",
                            "name",
                            "months",
                            "is_active",
                            "final_price_default",
                            "final_price_family",
                            "devices_limit_default",
                            "devices_limit_family",
                            "family_plan_enabled",
                        ],
                        "sort": "order",
                    }
                },
                "layout_options": {
                    "tabular": {
                        "widths": {
                            "id": 100,
                            "order": 90,
                            "name": 180,
                            "months": 110,
                            "is_active": 120,
                            "final_price_default": 180,
                            "final_price_family": 180,
                            "devices_limit_default": 180,
                            "devices_limit_family": 180,
                            "family_plan_enabled": 160,
                        }
                    }
                },
                "filter": None,
                "icon": "bookmark",
                "color": "#0EA5E9",
            }
        )
        upsert_preset(
            {
                "bookmark": "Тарифы: 12 месяцев + family",
                "user": None,
                "role": rid,
                "collection": "tariffs",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": [
                            "id",
                            "order",
                            "name",
                            "months",
                            "is_active",
                            "family_plan_enabled",
                            "final_price_default",
                            "final_price_family",
                            "devices_limit_default",
                            "devices_limit_family",
                        ],
                        "sort": "order",
                    }
                },
                "layout_options": {
                    "tabular": {
                        "widths": {
                            "id": 100,
                            "order": 90,
                            "name": 180,
                            "months": 110,
                            "is_active": 120,
                            "family_plan_enabled": 160,
                            "final_price_default": 180,
                            "final_price_family": 180,
                            "devices_limit_default": 180,
                            "devices_limit_family": 180,
                        }
                    }
                },
                "filter": {"months": {"_eq": 12}},
                "icon": "bookmark",
                "color": "#14B8A6",
            }
        )
        # promo_codes: default list
        promo_codes_tabular_fields = [
            "id",
            "name",
            "code_hmac",
            "batch_id",
            "disabled",
            "expires_at",
            "max_activations",
            "per_user_limit",
            "created_at",
        ]
        promo_codes_widths = {
            "id": 80,
            "name": 160,
            "code_hmac": 220,
            "batch_id": 100,
            "disabled": 100,
            "expires_at": 130,
            "max_activations": 120,
            "per_user_limit": 120,
            "created_at": 160,
        }
        upsert_preset(
            {
                "bookmark": None,
                "user": None,
                "role": rid,
                "collection": "promo_codes",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": promo_codes_tabular_fields,
                        "sort": "-created_at",
                    }
                },
                "layout_options": {"tabular": {"widths": promo_codes_widths}},
                "filter": None,
                "icon": "bookmark",
                "color": None,
            }
        )
        upsert_preset(
            {
                "bookmark": "Промо: активные",
                "user": None,
                "role": rid,
                "collection": "promo_codes",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": promo_codes_tabular_fields,
                        "sort": "-created_at",
                    }
                },
                "layout_options": {"tabular": {"widths": promo_codes_widths}},
                "filter": {
                    "_and": [
                        {"disabled": {"_eq": False}},
                        {
                            "_or": [
                                {"expires_at": {"_null": True}},
                                {"expires_at": {"_gte": "$NOW"}},
                            ]
                        },
                    ]
                },
                "icon": "bookmark",
                "color": "#10B981",
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
                        "fields": promo_codes_tabular_fields,
                        "sort": "-created_at",
                    }
                },
                "layout_options": {"tabular": {"widths": promo_codes_widths}},
                "filter": {"disabled": {"_eq": True}},
                "icon": "bookmark",
                "color": "#F59E0B",
            }
        )
        upsert_preset(
            {
                "bookmark": "Промо: истёкшие",
                "user": None,
                "role": rid,
                "collection": "promo_codes",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": promo_codes_tabular_fields,
                        "sort": "expires_at",
                    }
                },
                "layout_options": {"tabular": {"widths": promo_codes_widths}},
                "filter": {
                    "_and": [
                        {"expires_at": {"_nnull": True}},
                        {"expires_at": {"_lt": "$NOW"}},
                    ]
                },
                "icon": "bookmark",
                "color": "#EF4444",
            }
        )
        # promo_batches: default list
        promo_batches_tabular_fields = ["id", "title", "notes", "created_at", "created_by_id"]
        promo_batches_widths = {"id": 80, "title": 200, "notes": 200, "created_at": 160, "created_by_id": 120}
        upsert_preset(
            {
                "bookmark": None,
                "user": None,
                "role": rid,
                "collection": "promo_batches",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": promo_batches_tabular_fields,
                        "sort": "-created_at",
                    }
                },
                "layout_options": {"tabular": {"widths": promo_batches_widths}},
                "filter": None,
                "icon": "bookmark",
                "color": None,
            }
        )
        # promo_usages: default and latest
        promo_usages_tabular_fields = ["id", "promo_code_id", "user_id", "used_at", "context"]
        promo_usages_widths = {"id": 80, "promo_code_id": 120, "user_id": 140, "used_at": 180, "context": 150}
        upsert_preset(
            {
                "bookmark": None,
                "user": None,
                "role": rid,
                "collection": "promo_usages",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": promo_usages_tabular_fields,
                        "sort": "-used_at",
                    }
                },
                "layout_options": {"tabular": {"widths": promo_usages_widths}},
                "filter": None,
                "icon": "bookmark",
                "color": None,
            }
        )
        upsert_preset(
            {
                "bookmark": "Использование: последние",
                "user": None,
                "role": rid,
                "collection": "promo_usages",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": promo_usages_tabular_fields,
                        "sort": "-used_at",
                    }
                },
                "layout_options": {"tabular": {"widths": promo_usages_widths}},
                "filter": None,
                "icon": "bookmark",
                "color": "#8B5CF6",
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
        upsert_preset(
            {
                "bookmark": "Баг-репорты: все",
                "user": None,
                "role": rid,
                "collection": "error_reports",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["id", "created_at", "triage_severity", "triage_status", "type", "code", "user_id", "route", "message", "triage_owner"],
                        "sort": "-created_at",
                    }
                },
                "layout_options": {
                    "tabular": {
                        "widths": {
                            "id": 200,
                            "created_at": 180,
                            "triage_severity": 140,
                            "triage_status": 150,
                            "type": 150,
                            "code": 260,
                            "user_id": 150,
                            "route": 260,
                            "message": 360,
                            "triage_owner": 180,
                        }
                    }
                },
                "filter": None,
                "icon": "bookmark",
                "color": "#64748B",
            }
        )
        upsert_preset(
            {
                "bookmark": "Баг-репорты: не взяты",
                "user": None,
                "role": rid,
                "collection": "error_reports",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["id", "created_at", "triage_status", "type", "code", "route", "message"],
                        "sort": "-created_at",
                    }
                },
                "layout_options": {"tabular": {"widths": {"id": 200, "created_at": 180, "triage_status": 150, "type": 150, "code": 260, "route": 260, "message": 360}}},
                "filter": {"triage_status": {"_eq": "new"}},
                "icon": "bookmark",
                "color": "#EF4444",
            }
        )
        upsert_preset(
            {
                "bookmark": "Баг-репорты: просрочен triage (24ч)",
                "user": None,
                "role": rid,
                "collection": "error_reports",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["id", "created_at", "triage_due_at", "triage_severity", "triage_status", "type", "code", "route", "message"],
                        "sort": "triage_due_at",
                    }
                },
                "layout_options": {"tabular": {"widths": {"id": 200, "created_at": 180, "triage_due_at": 180, "triage_severity": 140, "triage_status": 150, "type": 150, "code": 260, "route": 260, "message": 360}}},
                "filter": {
                    "_and": [
                        {"triage_status": {"_eq": "new"}},
                        {"triage_due_at": {"_nnull": True}},
                        {"triage_due_at": {"_lte": "$NOW"}},
                    ]
                },
                "icon": "bookmark",
                "color": "#DC2626",
            }
        )
        upsert_preset(
            {
                "bookmark": "Баг-репорты: в работе",
                "user": None,
                "role": rid,
                "collection": "error_reports",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["id", "created_at", "triage_severity", "triage_status", "triage_owner", "type", "code", "route", "message"],
                        "sort": "-triage_updated_at",
                    }
                },
                "layout_options": {"tabular": {"widths": {"id": 200, "created_at": 180, "triage_severity": 140, "triage_status": 150, "triage_owner": 180, "type": 150, "code": 260, "route": 260, "message": 360}}},
                "filter": {"triage_status": {"_eq": "in_progress"}},
                "icon": "bookmark",
                "color": "#F59E0B",
            }
        )
        upsert_preset(
            {
                "bookmark": "Баг-репорты: исправлены",
                "user": None,
                "role": rid,
                "collection": "error_reports",
                "layout": "tabular",
                "layout_query": {
                    "tabular": {
                        "fields": ["id", "created_at", "triage_status", "triage_updated_at", "type", "code", "route", "message"],
                        "sort": "-triage_updated_at",
                    }
                },
                "layout_options": {"tabular": {"widths": {"id": 200, "created_at": 180, "triage_status": 150, "triage_updated_at": 180, "type": 150, "code": 260, "route": 260, "message": 360}}},
                "filter": {"triage_status": {"_eq": "resolved"}},
                "icon": "bookmark",
                "color": "#10B981",
            }
        )
        # in_app_notifications: default list + bookmarks
        notif_tabular_fields = ["id", "title", "is_active", "start_at", "end_at", "max_per_user", "max_per_session", "auto_hide_seconds", "created_at"]
        notif_widths = {
            "id": 80,
            "title": 240,
            "is_active": 100,
            "start_at": 180,
            "end_at": 180,
            "max_per_user": 170,
            "max_per_session": 170,
            "auto_hide_seconds": 160,
            "created_at": 160,
        }
        upsert_preset(
            {
                "bookmark": None,
                "user": None,
                "role": rid,
                "collection": "in_app_notifications",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": notif_tabular_fields, "sort": "-created_at"}},
                "layout_options": {"tabular": {"widths": notif_widths}},
                "filter": None,
                "icon": "bookmark",
                "color": None,
            }
        )
        upsert_preset(
            {
                "bookmark": "Уведомления: активные",
                "user": None,
                "role": rid,
                "collection": "in_app_notifications",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": notif_tabular_fields, "sort": "-created_at"}},
                "layout_options": {"tabular": {"widths": notif_widths}},
                "filter": {
                    "_and": [
                        {"is_active": {"_eq": True}},
                        {
                            "_or": [
                                {"end_at": {"_null": True}},
                                {"end_at": {"_gte": "$NOW"}},
                            ]
                        },
                    ]
                },
                "icon": "bookmark",
                "color": "#10B981",
            }
        )
        upsert_preset(
            {
                "bookmark": "Уведомления: неактивные",
                "user": None,
                "role": rid,
                "collection": "in_app_notifications",
                "layout": "tabular",
                "layout_query": {"tabular": {"fields": notif_tabular_fields, "sort": "-created_at"}},
                "layout_options": {"tabular": {"widths": notif_widths}},
                "filter": {"is_active": {"_eq": False}},
                "icon": "bookmark",
                "color": "#EF4444",
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


def get_content_redesign_collections() -> list[str]:
    return [
        "users",
        "active_tariffs",
        "tariffs",
        "family_members",
        "family_invites",
        "family_devices",
        "family_audit_logs",
        "promo_codes",
        "promo_batches",
        "promo_usages",
        "processed_payments",
        "in_app_notifications",
        "prize_wheel_config",
        "prize_wheel_history",
        "error_reports",
        "partner_withdrawals",
        "partner_qr_codes",
        "connections",
    ]


def cleanup_user_presets_for_scope(client: DirectusClient, collections: Iterable[str]) -> None:
    """
    Rollout helper:
    DIRECTUS_CONTENT_UX_CLEAN_USER_PRESETS=1 removes personal presets in scope
    so new role-level defaults become immediately visible.
    """
    if os.getenv("DIRECTUS_CONTENT_UX_CLEAN_USER_PRESETS", "0").strip() != "1":
        return

    target = {c for c in collections if isinstance(c, str) and c}
    if not target:
        return

    resp = client.get("/presets", params={"limit": 1200, "fields": "id,user,collection"})
    if resp.status_code in (401, 403):
        return
    resp.raise_for_status()
    rows = resp.json().get("data") or []
    removed = 0
    for row in rows:
        if row.get("collection") not in target:
            continue
        if not row.get("user"):
            continue
        preset_id = row.get("id")
        if not preset_id:
            continue
        deleted = client.delete(f"/presets/{preset_id}")
        if deleted.status_code in (401, 403, 404):
            continue
        deleted.raise_for_status()
        removed += 1

    if removed:
        print(f"Removed user-level presets in redesign scope: {removed}")


def ensure_role_presets(client: DirectusClient) -> None:
    """
    Redesigned role-level presets:
    - role-only defaults (no user-level preset writes),
    - legacy bookmark pruning by keep-set,
    - PK self-heal for tabular/cards.
    """
    role_map = get_role_map(client)
    admin_role = role_map.get("Administrator")
    manager_role = role_map.get("Manager")
    viewer_role = role_map.get("Viewer")
    if not admin_role and not manager_role and not viewer_role:
        return

    available_collections = set(list_collections(client))
    redesign_scope = set(get_content_redesign_collections()) & available_collections
    if not redesign_scope:
        return

    resp = client.get(
        "/presets",
        params={
            "limit": 1200,
            "fields": "id,role,user,collection,bookmark,layout,layout_query,layout_options,filter,color,icon,search",
        },
    )
    resp.raise_for_status()
    existing = resp.json().get("data") or []

    def ensure_tabular_fields_include_pk(preset: Dict[str, Any], *, pk_field: str = "id") -> Optional[Dict[str, Any]]:
        layout_query = preset.get("layout_query")
        if not isinstance(layout_query, dict):
            return {"tabular": {"fields": [pk_field]}}
        tabular = layout_query.get("tabular")
        if not isinstance(tabular, dict):
            return {"tabular": {"fields": [pk_field]}}
        fields = tabular.get("fields")
        if not isinstance(fields, list):
            return {**layout_query, "tabular": {**tabular, "fields": [pk_field]}}
        if pk_field in fields:
            return None
        return {**layout_query, "tabular": {**tabular, "fields": [pk_field, *fields]}}

    def ensure_cards_fields_include_pk(
        preset: Dict[str, Any],
        *,
        pk_field: str = "id",
        required_fields: Optional[list[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        layout_query = preset.get("layout_query")
        if not isinstance(layout_query, dict):
            layout_query = {}
        cards = layout_query.get("cards")
        if not isinstance(cards, dict):
            cards = {}
        fields = cards.get("fields")
        if not isinstance(fields, list):
            fields = []

        desired = [pk_field]
        if required_fields:
            for field in required_fields:
                if isinstance(field, str) and field and field not in desired:
                    desired.append(field)

        new_fields = list(fields)
        changed = False
        for field in reversed(desired):
            if field not in new_fields:
                new_fields.insert(0, field)
                changed = True
        if not changed:
            return None
        return {**layout_query, "cards": {**cards, "fields": new_fields}}

    # Auto-heal PK in existing presets for redesigned collections.
    for row in list(existing):
        if row.get("collection") not in redesign_scope:
            continue
        preset_id = row.get("id")
        if not preset_id:
            continue
        layout = row.get("layout")
        if layout == "tabular":
            new_layout = ensure_tabular_fields_include_pk(row, pk_field="id")
        elif layout == "cards":
            new_layout = ensure_cards_fields_include_pk(row, pk_field="id", required_fields=["username", "full_name"])
        else:
            continue
        if not new_layout:
            continue
        client.patch(f"/presets/{preset_id}", json={"layout_query": new_layout}).raise_for_status()
        row["layout_query"] = new_layout

    def upsert_preset(payload: Dict[str, Any]) -> None:
        collection = payload.get("collection")
        if not collection or collection not in available_collections:
            return
        key = (
            str(payload.get("role") or ""),
            str(payload.get("user") or ""),
            payload.get("collection"),
            payload.get("bookmark"),
        )
        found = next(
            (
                p
                for p in existing
                if (
                    str(p.get("role") or ""),
                    str(p.get("user") or ""),
                    p.get("collection"),
                    p.get("bookmark"),
                )
                == key
            ),
            None,
        )
        if found and found.get("id"):
            client.patch(f"/presets/{found['id']}", json=payload).raise_for_status()
            found.update(payload)
            return
        created = client.post("/presets", json=payload)
        created.raise_for_status()
        created_row = created.json().get("data") or {}
        existing.append(created_row)

    def prune_legacy_role_bookmarks(role_id: Any, collection: str, keep_set: set[str]) -> None:
        role_key = str(role_id)
        to_remove: list[Dict[str, Any]] = []
        for row in existing:
            if row.get("collection") != collection:
                continue
            if str(row.get("role") or "") != role_key:
                continue
            if row.get("user") is not None:
                continue
            bookmark = row.get("bookmark")
            if bookmark is None:
                continue
            if isinstance(bookmark, str) and bookmark in keep_set:
                continue
            to_remove.append(row)
        for row in to_remove:
            preset_id = row.get("id")
            if not preset_id:
                continue
            deleted = client.delete(f"/presets/{preset_id}")
            if deleted.status_code in (401, 403, 404):
                continue
            deleted.raise_for_status()
            try:
                existing.remove(row)
            except ValueError:
                pass

    width_map = {
        "id": 110,
        "username": 170,
        "full_name": 220,
        "balance": 120,
        "expired_at": 170,
        "is_subscribed": 130,
        "is_trial": 110,
        "is_blocked": 120,
        "hwid_limit": 140,
        "lte_gb_total": 140,
        "active_tariff_id": 140,
        "connected_at": 180,
        "registration_date": 180,
        "is_partner": 110,
        "referrals": 110,
        "user_id": 150,
        "name": 190,
        "months": 100,
        "price": 120,
        "lte_gb_used": 130,
        "devices_decrease_count": 170,
        "lte_price_per_gb": 150,
        "progressive_multiplier": 170,
        "residual_day_fraction": 170,
        "order": 90,
        "is_active": 120,
        "family_plan_enabled": 160,
        "final_price_default": 160,
        "final_price_family": 160,
        "devices_limit_default": 170,
        "devices_limit_family": 170,
        "lte_enabled": 120,
        "owner_id": 150,
        "member_id": 150,
        "status": 130,
        "allocated_devices": 170,
        "created_at": 170,
        "updated_at": 170,
        "max_uses": 110,
        "used_count": 110,
        "expires_at": 170,
        "revoked_at": 170,
        "title": 220,
        "subtitle": 260,
        "client_id": 190,
        "actor_id": 150,
        "action": 180,
        "target_id": 180,
        "batch_id": 120,
        "disabled": 110,
        "max_activations": 150,
        "per_user_limit": 150,
        "code_hmac": 260,
        "notes": 280,
        "created_by_id": 130,
        "promo_code_id": 160,
        "used_at": 180,
        "context": 240,
        "payment_id": 220,
        "amount": 120,
        "amount_external": 160,
        "amount_from_balance": 180,
        "start_at": 170,
        "end_at": 170,
        "max_per_user": 150,
        "max_per_session": 150,
        "auto_hide_seconds": 170,
        "prize_type": 150,
        "prize_name": 200,
        "prize_value": 130,
        "probability": 130,
        "requires_admin": 150,
        "is_claimed": 120,
        "is_rejected": 120,
        "admin_notified": 130,
        "triage_due_at": 170,
        "triage_severity": 140,
        "triage_status": 140,
        "triage_owner": 170,
        "type": 130,
        "code": 260,
        "route": 260,
        "message": 360,
        "amount_rub": 130,
        "method": 120,
        "error": 240,
        "slug": 170,
        "views_count": 130,
        "activations_count": 150,
        "at": 170,
    }

    def widths_for(fields: list[str]) -> Dict[str, int]:
        return {field: width_map.get(field, 160) for field in fields}

    def make_tabular(
        *,
        role_id: Any,
        collection: str,
        fields: list[str],
        sort: str,
        bookmark: Optional[str] = None,
        filter_query: Any = None,
        color: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_fields = list(fields)
        if "id" not in safe_fields:
            safe_fields = ["id", *safe_fields]
        return {
            "bookmark": bookmark,
            "user": None,
            "role": role_id,
            "collection": collection,
            "layout": "tabular",
            "layout_query": {"tabular": {"fields": safe_fields, "sort": sort}},
            "layout_options": {"tabular": {"widths": widths_for(safe_fields)}},
            "search": None,
            "filter": filter_query,
            "icon": "bookmark",
            "color": color,
        }

    def make_cards(
        *,
        role_id: Any,
        collection: str,
        fields: list[str],
        sort: str,
        bookmark: str,
        title: str,
        subtitle: str,
        color: Optional[str] = None,
    ) -> Dict[str, Any]:
        safe_fields = list(fields)
        if "id" not in safe_fields:
            safe_fields = ["id", *safe_fields]
        return {
            "bookmark": bookmark,
            "user": None,
            "role": role_id,
            "collection": collection,
            "layout": "cards",
            "layout_query": {"cards": {"fields": safe_fields, "sort": sort}},
            "layout_options": {"cards": {"title": title, "subtitle": subtitle, "icon": "person", "size": 2}},
            "search": None,
            "filter": None,
            "icon": "bookmark",
            "color": color,
        }

    matrix = {
        "users": {
            "default_fields": ["id", "username", "full_name", "balance", "expired_at", "is_subscribed", "is_trial", "is_blocked", "hwid_limit", "lte_gb_total", "active_tariff_id", "connected_at", "registration_date", "is_partner", "referrals"],
            "default_sort": "-registration_date",
            "bookmarks": [
                {
                    "bookmark": "Пользователи · Риск",
                    "sort": "-registration_date",
                    "filter": {"_or": [{"is_blocked": {"_eq": True}}, {"expired_at": {"_lte": "$NOW"}}]},
                    "color": "#F59E0B",
                },
                {
                    "bookmark": "Пользователи · Доход",
                    "sort": "-balance",
                    "filter": None,
                    "color": "#10B981",
                },
                {
                    "bookmark": "Пользователи · Карточки",
                    "layout": "cards",
                    "sort": "-registration_date",
                    "fields": ["id", "username", "full_name", "expired_at", "balance", "is_blocked", "is_subscribed", "registration_date"],
                    "color": "#3B82F6",
                },
            ],
        },
        "active_tariffs": {
            "default_fields": ["id", "user_id", "name", "months", "price", "hwid_limit", "lte_gb_total", "lte_gb_used", "devices_decrease_count", "lte_price_per_gb", "progressive_multiplier", "residual_day_fraction"],
            "default_sort": "-id",
            "bookmarks": [
                {"bookmark": "Активные тарифы · LTE usage", "sort": "-lte_gb_used", "filter": {"lte_gb_total": {"_gt": 0}}, "color": "#06B6D4"},
                {"bookmark": "Активные тарифы · Устройства", "sort": "-devices_decrease_count", "filter": None, "color": "#F59E0B"},
            ],
        },
        "tariffs": {
            "default_fields": ["id", "order", "name", "months", "is_active", "family_plan_enabled", "final_price_default", "final_price_family", "devices_limit_default", "devices_limit_family", "lte_enabled", "lte_price_per_gb"],
            "default_sort": "order",
            "bookmarks": [
                {"bookmark": "Тарифы · Семейные", "sort": "order", "filter": {"family_plan_enabled": {"_eq": True}}, "color": "#14B8A6"},
                {"bookmark": "Тарифы · Неактивные", "sort": "order", "filter": {"is_active": {"_eq": False}}, "color": "#EF4444"},
            ],
        },
        "family_members": {
            "default_fields": ["id", "owner_id", "member_id", "status", "allocated_devices", "created_at", "updated_at"],
            "default_sort": "-updated_at",
            "bookmarks": [
                {"bookmark": "Семья · Активные", "sort": "-updated_at", "filter": {"status": {"_eq": "active"}}, "color": "#10B981"},
                {"bookmark": "Семья · Последние изменения", "sort": "-updated_at", "filter": None, "color": "#06B6D4"},
            ],
        },
        "family_invites": {
            "default_fields": ["id", "owner_id", "allocated_devices", "max_uses", "used_count", "expires_at", "revoked_at", "created_at"],
            "default_sort": "-created_at",
            "bookmarks": [
                {
                    "bookmark": "Инвайты · Активные",
                    "sort": "-created_at",
                    "filter": {"_and": [{"revoked_at": {"_null": True}}, {"_or": [{"expires_at": {"_null": True}}, {"expires_at": {"_gte": "$NOW"}}]}]},
                    "color": "#10B981",
                },
                {"bookmark": "Инвайты · Использованные", "sort": "-created_at", "filter": {"used_count": {"_gt": 0}}, "color": "#F59E0B"},
            ],
        },
        "family_devices": {
            "default_fields": ["id", "user_id", "title", "subtitle", "client_id", "created_at"],
            "default_sort": "-created_at",
            "bookmarks": [{"bookmark": "Устройства · Последние", "sort": "-created_at", "filter": None, "color": "#06B6D4"}],
        },
        "family_audit_logs": {
            "default_fields": ["id", "owner_id", "actor_id", "action", "target_id", "created_at"],
            "default_sort": "-created_at",
            "bookmarks": [{"bookmark": "Аудит семьи · Последние", "sort": "-created_at", "filter": None, "color": "#64748B"}],
        },
        "promo_codes": {
            "default_fields": ["id", "name", "batch_id", "disabled", "expires_at", "max_activations", "per_user_limit", "code_hmac", "created_at"],
            "default_sort": "-created_at",
            "bookmarks": [
                {
                    "bookmark": "Промокоды · Активные",
                    "sort": "-created_at",
                    "filter": {"_and": [{"disabled": {"_eq": False}}, {"_or": [{"expires_at": {"_null": True}}, {"expires_at": {"_gte": "$NOW"}}]}]},
                    "color": "#10B981",
                },
                {"bookmark": "Промокоды · Истекшие", "sort": "expires_at", "filter": {"_and": [{"expires_at": {"_nnull": True}}, {"expires_at": {"_lt": "$NOW"}}]}, "color": "#EF4444"},
                {"bookmark": "Промокоды · Отключенные", "sort": "-created_at", "filter": {"disabled": {"_eq": True}}, "color": "#F59E0B"},
            ],
        },
        "promo_batches": {
            "default_fields": ["id", "title", "notes", "created_at", "created_by_id"],
            "default_sort": "-created_at",
            "bookmarks": [{"bookmark": "Партии промо · Последние", "sort": "-created_at", "filter": None, "color": "#8B5CF6"}],
        },
        "promo_usages": {
            "default_fields": ["id", "promo_code_id", "user_id", "used_at", "context"],
            "default_sort": "-used_at",
            "bookmarks": [{"bookmark": "Промо usage · Последние", "sort": "-used_at", "filter": None, "color": "#8B5CF6"}],
        },
        "processed_payments": {
            "default_fields": ["id", "payment_id", "user_id", "amount", "amount_external", "amount_from_balance", "status", "processed_at"],
            "default_sort": "-processed_at",
            "bookmarks": [
                {"bookmark": "Платежи · Крупные", "sort": "-amount", "filter": {"amount": {"_gt": 0}}, "color": "#10B981"},
                {"bookmark": "Платежи · Неуспешные", "sort": "-processed_at", "filter": {"status": {"_neq": "succeeded"}}, "color": "#EF4444"},
            ],
        },
        "in_app_notifications": {
            "default_fields": ["id", "title", "is_active", "start_at", "end_at", "max_per_user", "max_per_session", "auto_hide_seconds", "created_at"],
            "default_sort": "-created_at",
            "bookmarks": [
                {"bookmark": "Уведомления · Активные", "sort": "-created_at", "filter": {"_and": [{"is_active": {"_eq": True}}, {"_or": [{"end_at": {"_null": True}}, {"end_at": {"_gte": "$NOW"}}]}]}, "color": "#10B981"},
                {"bookmark": "Уведомления · Неактивные", "sort": "-created_at", "filter": {"is_active": {"_eq": False}}, "color": "#EF4444"},
            ],
        },
        "prize_wheel_config": {
            "default_fields": ["id", "prize_type", "prize_name", "prize_value", "probability", "is_active", "requires_admin", "updated_at"],
            "default_sort": "-updated_at",
            "bookmarks": [
                {"bookmark": "Призы · Активные", "sort": "-updated_at", "filter": {"is_active": {"_eq": True}}, "color": "#10B981"},
                {"bookmark": "Призы · Требуют админа", "sort": "-updated_at", "filter": {"requires_admin": {"_eq": True}}, "color": "#F59E0B"},
            ],
        },
        "prize_wheel_history": {
            "default_fields": ["id", "user_id", "prize_name", "prize_type", "prize_value", "is_claimed", "is_rejected", "admin_notified", "created_at"],
            "default_sort": "-created_at",
            "bookmarks": [
                {"bookmark": "История призов · Не обработаны", "sort": "-created_at", "filter": {"_and": [{"is_claimed": {"_eq": False}}, {"is_rejected": {"_eq": False}}]}, "color": "#F59E0B"},
                {"bookmark": "История призов · Отклонены", "sort": "-created_at", "filter": {"is_rejected": {"_eq": True}}, "color": "#EF4444"},
            ],
        },
        "error_reports": {
            "default_fields": ["id", "created_at", "triage_due_at", "triage_severity", "triage_status", "triage_owner", "type", "code", "route", "user_id", "message"],
            "default_sort": "-created_at",
            "bookmarks": [
                {"bookmark": "Ошибки · Новые", "sort": "-created_at", "filter": {"triage_status": {"_eq": "new"}}, "color": "#EF4444"},
                {"bookmark": "Ошибки · Просрочен triage", "sort": "triage_due_at", "filter": {"_and": [{"triage_status": {"_eq": "new"}}, {"triage_due_at": {"_nnull": True}}, {"triage_due_at": {"_lte": "$NOW"}}]}, "color": "#DC2626"},
                {"bookmark": "Ошибки · В работе", "sort": "-created_at", "filter": {"triage_status": {"_eq": "in_progress"}}, "color": "#F59E0B"},
            ],
        },
        "partner_withdrawals": {
            "default_fields": ["id", "owner_id", "amount_rub", "method", "status", "error", "created_at", "updated_at"],
            "default_sort": "-created_at",
            "bookmarks": [
                {"bookmark": "Партнерка · Выводы в ожидании", "sort": "-created_at", "filter": {"status": {"_in": ["created", "processing"]}}, "color": "#F59E0B"},
                {"bookmark": "Партнерка · Выводы завершенные", "sort": "-updated_at", "filter": {"status": {"_eq": "success"}}, "color": "#10B981"},
            ],
        },
        "partner_qr_codes": {
            "default_fields": ["id", "owner_id", "title", "slug", "is_active", "views_count", "activations_count", "created_at", "updated_at"],
            "default_sort": "-updated_at",
            "bookmarks": [
                {"bookmark": "Партнерка · Активные QR", "sort": "-updated_at", "filter": {"is_active": {"_eq": True}}, "color": "#10B981"},
                {"bookmark": "Партнерка · Топ активаций", "sort": "-activations_count", "filter": None, "color": "#06B6D4"},
            ],
        },
        "connections": {
            "default_fields": ["id", "user_id", "at"],
            "default_sort": "-at",
            "bookmarks": [{"bookmark": "Подключения · Последние", "sort": "-at", "filter": None, "color": "#06B6D4"}],
        },
    }

    target_roles: list[Dict[str, Any]] = []
    if admin_role:
        target_roles.append(admin_role)
    if manager_role:
        target_roles.append(manager_role)

    for role in target_roles:
        role_id = role["id"]
        for collection, config in matrix.items():
            if collection not in available_collections:
                continue

            default_fields = list(config["default_fields"])
            default_sort = str(config["default_sort"])
            bookmark_defs = list(config.get("bookmarks") or [])
            keep_set = {str(item["bookmark"]) for item in bookmark_defs if item.get("bookmark")}
            prune_legacy_role_bookmarks(role_id, collection, keep_set)

            upsert_preset(
                make_tabular(
                    role_id=role_id,
                    collection=collection,
                    fields=default_fields,
                    sort=default_sort,
                )
            )

            for item in bookmark_defs:
                layout = str(item.get("layout") or "tabular")
                if layout == "cards":
                    upsert_preset(
                        make_cards(
                            role_id=role_id,
                            collection=collection,
                            fields=list(item.get("fields") or default_fields),
                            sort=str(item.get("sort") or default_sort),
                            bookmark=str(item["bookmark"]),
                            title="{{ username }}",
                            subtitle="{{ full_name }}",
                            color=item.get("color"),
                        )
                    )
                    continue
                upsert_preset(
                    make_tabular(
                        role_id=role_id,
                        collection=collection,
                        fields=default_fields,
                        sort=str(item.get("sort") or default_sort),
                        bookmark=str(item["bookmark"]),
                        filter_query=item.get("filter"),
                        color=item.get("color"),
                    )
                )

    if viewer_role:
        rid = viewer_role["id"]
        viewer_defaults = [
            make_tabular(
                role_id=rid,
                collection="users",
                fields=["id", "username", "full_name", "registration_date", "expired_at", "is_blocked"],
                sort="-registration_date",
            ),
            make_tabular(
                role_id=rid,
                collection="connections",
                fields=["id", "user_id", "at"],
                sort="-at",
            ),
        ]
        viewer_bookmarks = [
            make_tabular(
                role_id=rid,
                collection="connections",
                fields=["id", "user_id", "at"],
                sort="-at",
                bookmark="Подключения · Последние",
                color="#06B6D4",
            )
        ]
        viewer_bookmark_keep: Dict[str, set[str]] = {
            "users": set(),
            "connections": {"Подключения · Последние"},
        }
        for payload in viewer_defaults:
            collection = payload.get("collection")
            if collection not in available_collections:
                continue
            prune_legacy_role_bookmarks(rid, str(collection), viewer_bookmark_keep.get(str(collection), set()))
            upsert_preset(payload)
        for payload in viewer_bookmarks:
            collection = payload.get("collection")
            if collection not in available_collections:
                continue
            upsert_preset(payload)


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


def verify_users_item_access(client: DirectusClient) -> None:
    """
    Diagnostic guard:
    list view can work while item view is forbidden due policy mismatch.
    """
    sample_resp = client.get("/items/users", params={"limit": 1, "fields": "id"})
    if sample_resp.status_code in (401, 403):
        print("WARN: users list access is forbidden for the current auth context.")
        return
    sample_resp.raise_for_status()
    rows = sample_resp.json().get("data") or []
    if not rows:
        return
    sample_id = rows[0].get("id")
    if sample_id is None:
        print("WARN: users list returned rows without id.")
        return
    item_resp = client.get(f"/items/users/{sample_id}", params={"fields": "id"})
    if item_resp.status_code in (401, 403):
        print(
            "WARN: users item view is forbidden while list is available. "
            "Check role/policy links in Directus Access."
        )
        return
    item_resp.raise_for_status()


def verify_tariffs_form_visibility(client: DirectusClient) -> None:
    """
    Safety check: make sure key tariffs fields are visible after UX setup.
    """
    required_fields = [
        "name",
        "months",
        "order",
        "is_active",
        "family_plan_enabled",
        "final_price_default",
        "final_price_family",
        "devices_limit_default",
        "devices_limit_family",
        "base_price",
        "progressive_multiplier",
        "lte_enabled",
        "lte_price_per_gb",
    ]
    for field in required_fields:
        resp = client.get(f"/fields/tariffs/{field}", params={"fields": "field,meta"})
        if resp.status_code in (401, 403, 404):
            print(f"WARN: tariffs field {field} not readable (status={resp.status_code})")
            continue
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        meta = data.get("meta") or {}
        if bool(meta.get("hidden", False)):
            print(f"WARN: tariffs field {field} is hidden; forcing visible may be required.")


def main() -> None:
    if load_dotenv:
        load_dotenv()

    base_url = env("DIRECTUS_URL")  # e.g. http://37.230.114.122:8055
    email = env("DIRECTUS_ADMIN_EMAIL")
    password = env("DIRECTUS_ADMIN_PASSWORD")

    auth = login(base_url, email, password)
    client = DirectusClient(auth)

    phases: list[tuple[str, Callable[[], None]]] = [
        ("set_language_ru", lambda: set_language_ru(client)),
        ("ensure_permissions_baseline", lambda: ensure_permissions_baseline(client)),
        ("ensure_nav_groups", lambda: ensure_nav_groups(client)),
        ("ensure_nav_group_permissions", lambda: ensure_nav_group_permissions(client)),
        ("apply_collection_ux", lambda: apply_collection_ux(client)),
        ("apply_field_notes_ru", lambda: apply_field_notes_ru(client)),
        ("apply_error_reports_form_ux", lambda: apply_error_reports_form_ux(client)),
        ("apply_tariffs_form_ux", lambda: apply_tariffs_form_ux(client)),
        ("ensure_tariffs_presentation_dividers", lambda: ensure_tariffs_presentation_dividers(client)),
        ("ensure_users_relations_ux", lambda: ensure_users_relations_ux(client)),
        ("apply_users_form_ux", lambda: apply_users_form_ux(client)),
        ("ensure_users_presentation_dividers", lambda: ensure_users_presentation_dividers(client)),
        ("apply_users_luxury_ux", lambda: apply_users_luxury_ux(client)),
        ("ensure_users_family_section_ux", lambda: ensure_users_family_section_ux(client)),
        ("ensure_users_family_workspace_aliases", lambda: ensure_users_family_workspace_aliases(client)),
        ("ensure_admin_settings", lambda: ensure_admin_settings(client)),
        # Re-run baseline after all late-created collections to avoid skipped grants.
        ("ensure_permissions_baseline_post", lambda: ensure_permissions_baseline(client)),
        ("ensure_insights_dashboard", lambda: ensure_insights_dashboard(client)),
        ("cleanup_user_presets_for_scope", lambda: cleanup_user_presets_for_scope(client, get_content_redesign_collections())),
        ("ensure_role_presets", lambda: ensure_role_presets(client)),
        ("enable_extension_tvpn_content_ops", lambda: ensure_extension_enabled(client, "tvpn-content-ops")),
        ("verify_users_item_access", lambda: verify_users_item_access(client)),
        ("verify_users_family_section_visibility", lambda: verify_users_family_section_visibility(client)),
        ("verify_tariffs_form_visibility", lambda: verify_tariffs_form_visibility(client)),
        ("enable_extension_tvpn_home", lambda: ensure_extension_enabled(client, "tvpn-home")),
        ("enable_extension_server_ops", lambda: ensure_extension_enabled(client, "server-ops")),
        ("enable_extension_id_link_editor", lambda: ensure_extension_enabled(client, "id-link-editor")),
    ]

    for phase_name, phase_fn in phases:
        print(f"Phase start: {phase_name}")
        phase_fn()
        _phase_pause()

    print("Directus super-setup completed successfully.")


if __name__ == "__main__":
    main()

"""Cashback fraud screening: detect referral self-dealing via HWID overlap.

Without this guard a partner can register a second account, mark themselves as
the referrer, pay from the second account, and harvest cashback to their own
partner balance. Whenever referrer and referred share a HWID we freeze the
PartnerEarnings row at status='pending_review' and surface it to admins in the
bot for explicit approve/reject (with a "contact partner" deeplink).
"""

from __future__ import annotations

import logging
from typing import Any

from bloobcat.db.hwid_local import HwidDeviceLocal
from bloobcat.db.user_devices import UserDevice
from bloobcat.db.users import Users

logger = logging.getLogger(__name__)


async def _safe_values_list(query) -> list:
    """Run a Tortoise values_list query, returning [] on any failure.

    Cashback screening must be fail-open: if HWID tables are missing or DB is
    flaky, we'd rather pay the partner than block legitimate cashback while the
    operator investigates. Frozen-by-mistake cashback is more disruptive than
    occasionally missing a self-dealing detection.
    """

    try:
        return list(await query)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cashback_review_hwid_query_failed err=%s", exc)
        return []


async def _collect_user_hwids(user: Users) -> set[str]:
    """Union of HWIDs seen for this user across local inventories.

    Fail-open by design — any error collecting HWIDs returns an empty set so
    cashback flow keeps running. Operationally we'd rather pay legitimate
    partners than freeze on a missing-table or DB-flake.
    """

    hwids: set[str] = set()

    try:
        user_id = int(user.id) if user.id is not None else None
        remnawave_uuid = getattr(user, "remnawave_uuid", None)

        if user_id is not None:
            ud_rows = await _safe_values_list(
                UserDevice.filter(user_id=user_id).values_list("hwid", flat=True)
            )
            hwids.update(str(h) for h in ud_rows if h)

            local_by_tg = await _safe_values_list(
                HwidDeviceLocal.filter(telegram_user_id=user_id).values_list(
                    "hwid", flat=True
                )
            )
            hwids.update(str(h) for h in local_by_tg if h)

        if remnawave_uuid:
            local_by_uuid = await _safe_values_list(
                HwidDeviceLocal.filter(user_uuid=remnawave_uuid).values_list(
                    "hwid", flat=True
                )
            )
            hwids.update(str(h) for h in local_by_uuid if h)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cashback_review_hwid_collection_failed err=%s", exc)
        return set()

    return {h.strip() for h in hwids if h and h.strip()}


async def _safe_detect(referrer: Users, referred: Users) -> dict[str, Any]:
    """Outer fail-open wrapper used by `_award_partner_cashback` injection."""

    try:
        return await detect_referral_overlap_signals(referrer, referred)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cashback_review_detect_failed err=%s", exc)
        return {"hwid_overlap": [], "same_telegram_id": False}


async def detect_referral_overlap_signals(
    referrer: Users, referred: Users
) -> dict[str, Any]:
    """Collect fraud signals between a referrer and the user that just paid.

    Returns a JSON-safe dict shaped like::

        {
            "hwid_overlap": ["...", "..."],   # shared HWID strings (may be empty)
            "same_telegram_id": False,        # always False unless DB is corrupt
        }
    """

    signals: dict[str, Any] = {"hwid_overlap": [], "same_telegram_id": False}

    if not referrer or not referred:
        return signals

    if int(referrer.id or 0) == int(referred.id or 0):
        signals["same_telegram_id"] = True
        return signals

    referrer_hwids = await _collect_user_hwids(referrer)
    referred_hwids = await _collect_user_hwids(referred)

    overlap = sorted(referrer_hwids & referred_hwids)
    signals["hwid_overlap"] = overlap

    return signals


def should_freeze_cashback(signals: dict[str, Any]) -> bool:
    """Decide whether to freeze a PartnerEarnings row for admin review."""

    if not signals:
        return False
    if signals.get("same_telegram_id"):
        return True
    overlap = signals.get("hwid_overlap") or []
    return bool(overlap)


DEFAULT_GOLDEN_THRESHOLDS: dict[str, int] = {
    "ip_cidr": 24,
    "tg_id_distance": 5,
    "registration_window_seconds": 60,
}

# Priority order for the `primary_reason` decision when multiple signals fire.
# HWID is the strongest signal (real device fingerprint), then TG-id family
# (proxies for sock-puppet farms), then network-level overlaps.
_GOLDEN_REASON_PRIORITY = (
    "hwid_overlap",
    "tg_family",
    "ip_block",
    "device_fp",
    "velocity",
)


def _ip_to_cidr_block(ip: str | None, prefix: int) -> str | None:
    """Project an IPv4 address onto its /prefix block as a normalized string."""
    if not ip:
        return None
    try:
        from ipaddress import ip_address, ip_network

        addr = ip_address(str(ip).strip())
        if addr.version != 4:  # IPv6 disabled for this comparison
            return str(addr)
        net = ip_network(f"{addr}/{int(prefix)}", strict=False)
        return str(net.network_address)
    except Exception:  # noqa: BLE001 - fail-open: caller treats None as no match
        return None


async def _collect_user_ip_blocks(
    user: Users, *, prefix: int
) -> set[str]:
    """Project a user's recently-seen IPs onto their /prefix CIDR blocks.

    Reads from the `connections` table when available — that's the pure-data
    table where session metadata is recorded. Fail-open on any error.
    """
    try:
        from bloobcat.db.user_devices import UserDevice  # noqa: WPS433

        rows = await _safe_values_list(
            UserDevice.filter(user_id=int(user.id))
            .exclude(metadata__isnull=True)
            .values_list("metadata", flat=True)
        )
    except Exception:  # noqa: BLE001
        rows = []

    blocks: set[str] = set()
    for raw in rows:
        ip = None
        if isinstance(raw, dict):
            ip = (
                raw.get("ip")
                or raw.get("last_ip")
                or raw.get("client_ip")
                or raw.get("source_ip")
            )
        if not ip:
            continue
        block = _ip_to_cidr_block(str(ip), prefix)
        if block:
            blocks.add(block)
    return blocks


async def _collect_user_device_fingerprints(user: Users) -> set[str]:
    """Hashes of (user_agent, platform) across the user's PushSubscription rows."""
    try:
        from hashlib import sha256

        from bloobcat.db.push_subscriptions import PushSubscription  # noqa: WPS433

        rows = await _safe_values_list(
            PushSubscription.filter(user_id=int(user.id)).values_list(
                "user_agent", "platform"
            )
        )
    except Exception:  # noqa: BLE001
        return set()

    fingerprints: set[str] = set()
    for ua, platform in rows or []:
        if not ua:
            continue
        material = f"{str(ua).strip().lower()}|{str(platform or '').strip().lower()}"
        fingerprints.add(sha256(material.encode("utf-8")).hexdigest()[:16])
    return fingerprints


def _tg_id_distance(referrer: Users, referred: Users) -> int | None:
    """Absolute |tg_id - tg_id| for users registered via Telegram. None for web users."""
    try:
        a = int(referrer.id or 0)
        b = int(referred.id or 0)
    except Exception:  # noqa: BLE001
        return None
    if a <= 0 or b <= 0:
        return None
    # Web-only users are above this floor — exclude from the family heuristic.
    if a >= 8_000_000_000_000_000 or b >= 8_000_000_000_000_000:
        return None
    return abs(a - b)


def _registration_velocity_seconds(
    referrer: Users, referred: Users
) -> int | None:
    """Seconds between the two registrations. None if either is missing the field."""
    a = getattr(referrer, "registration_date", None)
    b = getattr(referred, "registration_date", None)
    if a is None or b is None:
        return None
    try:
        delta = (a - b).total_seconds()
    except Exception:  # noqa: BLE001
        return None
    return int(abs(delta))


async def detect_golden_overlap_signals(
    referrer: Users,
    referred: Users,
    thresholds: dict | None = None,
) -> dict[str, Any]:
    """Collect Golden-Period clawback signals between a referrer and a referred user.

    Pure detection — does NOT mutate any state. The caller (clawback scanner)
    decides whether to act on `should_clawback`. Fail-open on any error so a
    transient DB issue can't lock up the scanner.

    Returns a JSON-safe dict:

        {
            "hwid_overlap": bool,
            "ip_block_overlap": bool,
            "device_fingerprint_overlap": bool,
            "tg_id_family": bool,
            "registration_velocity": bool,
            "should_clawback": bool,
            "primary_reason": str,           # "hwid_overlap" | ... | "none"
            "snapshot": {                    # raw values for audit
                "hwid_overlap_count": int,
                "ip_blocks_overlap": [str],
                "device_fingerprint_overlap": [str],
                "tg_id_distance": int | None,
                "registration_velocity_seconds": int | None,
                "thresholds": {...},
            }
        }
    """
    merged_thresholds = dict(DEFAULT_GOLDEN_THRESHOLDS)
    if thresholds:
        for k, v in thresholds.items():
            if v is not None:
                try:
                    merged_thresholds[str(k)] = int(v)
                except (TypeError, ValueError):
                    continue

    out: dict[str, Any] = {
        "hwid_overlap": False,
        "ip_block_overlap": False,
        "device_fingerprint_overlap": False,
        "tg_id_family": False,
        "registration_velocity": False,
        "should_clawback": False,
        "primary_reason": "none",
        "snapshot": {
            "hwid_overlap_count": 0,
            "ip_blocks_overlap": [],
            "device_fingerprint_overlap": [],
            "tg_id_distance": None,
            "registration_velocity_seconds": None,
            "thresholds": dict(merged_thresholds),
        },
    }

    if not referrer or not referred:
        return out

    if int(referrer.id or 0) == int(referred.id or 0):
        # Same user — treat as the strongest possible signal.
        out["hwid_overlap"] = True
        out["should_clawback"] = True
        out["primary_reason"] = "hwid_overlap"
        return out

    try:
        # 1. HWID overlap (re-uses the existing private)
        a_hwids = await _collect_user_hwids(referrer)
        b_hwids = await _collect_user_hwids(referred)
        hwid_overlap = sorted(a_hwids & b_hwids)
        if hwid_overlap:
            out["hwid_overlap"] = True
            out["snapshot"]["hwid_overlap_count"] = len(hwid_overlap)

        # 2. IP /CIDR overlap from user_devices.metadata.
        ip_prefix = int(merged_thresholds.get("ip_cidr", 24) or 24)
        a_blocks = await _collect_user_ip_blocks(referrer, prefix=ip_prefix)
        b_blocks = await _collect_user_ip_blocks(referred, prefix=ip_prefix)
        ip_overlap = sorted(a_blocks & b_blocks)
        if ip_overlap:
            out["ip_block_overlap"] = True
            out["snapshot"]["ip_blocks_overlap"] = ip_overlap[:5]

        # 3. Device fingerprint hash overlap from push_subscriptions.user_agent.
        a_fp = await _collect_user_device_fingerprints(referrer)
        b_fp = await _collect_user_device_fingerprints(referred)
        fp_overlap = sorted(a_fp & b_fp)
        if fp_overlap:
            out["device_fingerprint_overlap"] = True
            out["snapshot"]["device_fingerprint_overlap"] = fp_overlap[:5]

        # 4. TG ID family proximity.
        tg_distance = _tg_id_distance(referrer, referred)
        out["snapshot"]["tg_id_distance"] = tg_distance
        if (
            tg_distance is not None
            and tg_distance > 0
            and tg_distance
            < int(merged_thresholds.get("tg_id_distance", 5) or 5)
        ):
            out["tg_id_family"] = True

        # 5. Registration velocity.
        velocity = _registration_velocity_seconds(referrer, referred)
        out["snapshot"]["registration_velocity_seconds"] = velocity
        if velocity is not None and velocity < int(
            merged_thresholds.get("registration_window_seconds", 60) or 60
        ):
            out["registration_velocity"] = True
    except Exception as exc:  # noqa: BLE001 - fail-open
        logger.debug("golden_signals_collection_failed err=%s", exc)
        return out

    # Decision: any single strong signal triggers a clawback. Velocity alone
    # is the weakest and only triggers when paired with another signal.
    triggered: set[str] = set()
    if out["hwid_overlap"]:
        triggered.add("hwid_overlap")
    if out["tg_id_family"]:
        triggered.add("tg_family")
    if out["ip_block_overlap"]:
        triggered.add("ip_block")
    if out["device_fingerprint_overlap"]:
        triggered.add("device_fp")
    if out["registration_velocity"] and triggered:
        triggered.add("velocity")

    if triggered:
        for reason in _GOLDEN_REASON_PRIORITY:
            if reason in triggered:
                out["primary_reason"] = reason
                break
        out["should_clawback"] = True

    return out


def build_admin_review_text(
    *,
    earning_id: str,
    referrer: Users,
    referred: Users,
    amount_total_rub: int,
    reward_rub: int,
    percent: int,
    signals: dict[str, Any],
) -> str:
    """Render a Telegram-friendly admin card explaining why a cashback was frozen."""

    referrer_handle = (
        f"@{referrer.username}" if getattr(referrer, "username", None) else "(нет username)"
    )
    referred_handle = (
        f"@{referred.username}" if getattr(referred, "username", None) else "(нет username)"
    )

    overlap = signals.get("hwid_overlap") or []
    overlap_lines = "\n".join(f"  • <code>{h}</code>" for h in overlap[:5]) or "  (нет)"
    if len(overlap) > 5:
        overlap_lines += f"\n  ... и ещё {len(overlap) - 5}"

    same_tg = "ДА" if signals.get("same_telegram_id") else "нет"

    return (
        "🚨 <b>Партнёрский кешбэк заморожен</b>\n"
        "Сработал детектор self-dealing — нужно принять решение вручную.\n\n"
        f"💰 Сумма платежа: <b>{amount_total_rub} ₽</b>\n"
        f"🎁 Кешбэк партнёру: <b>{reward_rub} ₽</b> ({percent}%)\n\n"
        f"🤝 <b>Партнёр-приглашающий:</b>\n"
        f"  ID <code>{referrer.id}</code> {referrer_handle}\n\n"
        f"👤 <b>Приглашённый (платил):</b>\n"
        f"  ID <code>{referred.id}</code> {referred_handle}\n\n"
        f"🔍 <b>Сигналы:</b>\n"
        f"  Тот же telegram_id: {same_tg}\n"
        f"  Совпадающие HWID:\n{overlap_lines}\n\n"
        f"<i>earning_id: <code>{earning_id}</code></i>"
    )

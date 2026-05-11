from __future__ import annotations

import uuid
from urllib.parse import quote

from bloobcat.bot.bot import get_bot_username
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import telegram_settings
from tortoise.expressions import F


logger = get_logger("referral_attribution")

PARTNER_SOURCE_UTM = "partner"


def normalize_source_utm(raw: str | None) -> str:
    return (raw or "").strip()


def is_partner_source_utm(raw: str | None) -> bool:
    normalized = normalize_source_utm(raw)
    return normalized == PARTNER_SOURCE_UTM or normalized.startswith("qr_")


def is_campaign_utm(raw: str | None) -> bool:
    """Return True if `raw` is a specific campaign tag worth propagating downstream.

    A campaign tag is any non-empty utm value that is NOT the generic
    `partner` marker. `qr_*` campaign tokens, custom UTM strings, and any other
    non-generic source qualify; the bare `partner` marker does not, since it
    carries no campaign provenance.
    """
    normalized = normalize_source_utm(raw)
    if not normalized:
        return False
    return normalized != PARTNER_SOURCE_UTM


def pick_attribution_utm(
    current_utm: str | None,
    incoming_utm: str | None,
    *,
    force_partner_source: bool = False,
    referrer_utm: str | None = None,
) -> str | None:
    current = normalize_source_utm(current_utm)
    incoming = normalize_source_utm(incoming_utm)

    if not incoming:
        result: str | None = current or None
    elif force_partner_source and is_partner_source_utm(incoming):
        # Preserve an already-bound QR token over a later generic partner marker.
        if current.startswith("qr_") and incoming == PARTNER_SOURCE_UTM:
            result = current
        else:
            result = incoming
    elif current:
        result = current
    else:
        result = incoming

    # Downstream attribution: when the user's resolved utm is empty or just
    # the generic `partner` marker, inherit the campaign tag from the referrer
    # so multi-hop conversions stay attributed to the original campaign source.
    if referrer_utm and is_campaign_utm(referrer_utm) and not is_campaign_utm(result):
        result = normalize_source_utm(referrer_utm)

    return result or None


PartnerLinkMode = str  # narrowed to {"bot", "app"} at boundaries

DEFAULT_PARTNER_LINK_MODE: PartnerLinkMode = "bot"


def normalize_partner_link_mode(raw: str | None) -> PartnerLinkMode:
    value = (raw or "").strip().lower()
    if value == "app":
        return "app"
    return "bot"


async def _resolve_bot_name() -> str:
    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "VectraConnect_bot"
    return bot_name


async def _build_app_start_link(payload: str) -> str:
    webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
    if webapp_url and webapp_url.lower().startswith("https://"):
        sep = "&" if "?" in webapp_url else "?"
        return f"{webapp_url.rstrip('/')}{sep}startapp={quote(payload, safe='')}"
    bot_name = await _resolve_bot_name()
    return f"https://t.me/{bot_name}/start?startapp={quote(payload, safe='')}"


async def _build_chat_start_link(payload: str) -> str:
    bot_name = await _resolve_bot_name()
    return f"https://t.me/{bot_name}?start={quote(payload, safe='')}"


async def build_start_link_for_mode(payload: str, mode: PartnerLinkMode) -> str:
    if normalize_partner_link_mode(mode) == "app":
        return await _build_app_start_link(payload)
    return await _build_chat_start_link(payload)


async def _build_start_link(payload: str) -> str:
    """Legacy default link form for warm in-Telegram referrals (Mini App direct)."""
    return await _build_app_start_link(payload)


async def build_referral_link(user_id: int) -> str:
    # User referrals stay on direct Mini App: warm in-Telegram traffic, +1 tap is too costly.
    return await _build_start_link(str(user_id))


async def build_partner_link(
    user_id: int, mode: PartnerLinkMode = DEFAULT_PARTNER_LINK_MODE
) -> str:
    return await build_start_link_for_mode(f"{PARTNER_SOURCE_UTM}-{user_id}", mode)


async def _resolve_numeric_referrer(
    referrer_id: int,
) -> tuple[int, str | None]:
    referrer = await Users.get_or_none(id=int(referrer_id))
    if not referrer:
        return 0, None
    if bool(getattr(referrer, "is_partner", False)):
        return int(referrer_id), PARTNER_SOURCE_UTM
    return int(referrer_id), None


async def _resolve_partner_qr_start_param(
    param: str,
    user_id: int | None = None,
    *,
    track_view: bool = False,
) -> tuple[int, str | None]:
    token = param[3:]
    qr = None
    try:
        qr_uuid = uuid.UUID(token) if len(token) != 32 else uuid.UUID(hex=token)
        qr = await PartnerQr.get_or_none(id=qr_uuid)
    except Exception:
        qr = None
    if not qr:
        qr = await PartnerQr.get_or_none(slug=token)
    if qr and track_view:
        try:
            await PartnerQr.filter(id=qr.id).update(
                views_count=F("views_count") + 1
            )
        except Exception as exc:
            logger.warning("Failed to update partner QR views for %s: %s", qr.id, exc)
    referred_by = int(qr.owner_id) if qr else 0
    logger.debug(
        "Resolved partner QR token=%s user=%s referrer=%s",
        token,
        user_id,
        referred_by,
    )
    return referred_by, param


async def resolve_referral_from_start_param(
    param_raw: str | None,
    *,
    user_id: int | None = None,
    track_partner_qr_view: bool = False,
) -> tuple[int, str | None]:
    param = (param_raw or "").strip()
    if not param:
        return 0, None

    if param.startswith("family_"):
        return 0, None

    if param.startswith("qr_"):
        return await _resolve_partner_qr_start_param(
            param,
            user_id=user_id,
            track_view=track_partner_qr_view,
        )

    if param.startswith("ref_") and param[len("ref_") :].isdigit():
        return int(param[len("ref_") :]), None

    if param.startswith("ref-") and param[len("ref-") :].isdigit():
        return int(param[len("ref-") :]), None

    if param.startswith(f"{PARTNER_SOURCE_UTM}-"):
        ref_part = param[len(PARTNER_SOURCE_UTM) + 1 :]
        if not ref_part.isdigit():
            # «partner-<не_число>» — невалидный suffix; не сохраняем мусор как utm,
            # т.к. это значение протекает в campaign-inheritance и попадает в
            # админский CSV (admin-widgets UTM-stats), что портит когорты.
            return 0, None
        referrer_id, partner_marker = await _resolve_numeric_referrer(int(ref_part))
        if not referrer_id or not partner_marker:
            return 0, None
        return referrer_id, partner_marker

    if "-" in param:
        utm_part, ref_part = param.rsplit("-", 1)
        if ref_part.isdigit():
            return int(ref_part), utm_part or None
        return 0, param

    if param.isdigit():
        return await _resolve_numeric_referrer(int(param))

    return 0, param

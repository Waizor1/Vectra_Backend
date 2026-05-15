from datetime import date
from typing import Any, Dict, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from bloobcat.bot.bot import get_bot_username
from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.logger import get_logger
from bloobcat.services.home_screen_rewards import (
    HOME_SCREEN_BALANCE_BONUS_RUB,
    HOME_SCREEN_DISCOUNT_PERCENT,
    HOME_SCREEN_DISCOUNT_TTL_DAYS,
    claim_home_screen_reward,
    repair_home_screen_reward,
    scan_home_screen_orphans,
)
from bloobcat.services.referral_gamification import (
    build_referral_status,
    open_referral_chest,
)
from bloobcat.services.story_image_renderer import (
    STORY_IMAGE_HEIGHT,
    STORY_IMAGE_WIDTH,
    render_story_image,
)
from bloobcat.services.story_referral import (
    encode_story_code,
    encoded_story_code_length,
    materialize_user_story_code,
)
from bloobcat.settings import script_settings, telegram_settings

logger = get_logger("routes.referrals")

router = APIRouter(prefix="/referrals", tags=["referrals"])


class ReferralLevelInfo(BaseModel):
    key: Literal["start", "bronze", "silver", "gold", "platinum", "diamond"] | str
    name: str
    threshold: int
    cashbackPercent: int


class ReferralNextLevelInfo(ReferralLevelInfo):
    friendsLeft: int


class ReferralLevelRow(ReferralLevelInfo):
    chestRewardLabel: str
    reached: bool


class ReferralPendingChest(BaseModel):
    id: int
    levelKey: str
    levelName: str
    title: str


class ReferralRewardHistoryItem(BaseModel):
    type: Literal["cashback", "chest"]
    title: str
    valueLabel: str
    createdAt: str


class GoldenPeriodInviteeRow(BaseModel):
    id: str
    displayName: str
    status: Literal["waiting", "paid", "clawed_back"] | str
    paidAtMs: Optional[int] = None
    clawbackReason: Optional[str] = None


class GoldenPeriodPayload(BaseModel):
    active: bool
    startedAtMs: int
    expiresAtMs: int
    cap: int
    paidOutCount: int
    totalPaidRub: int
    payoutAmount: int
    seen: bool
    invitees: list[GoldenPeriodInviteeRow]


class ReferralStatusResponse(BaseModel):
    referralLink: str
    friendsCount: int
    invitedCount: int
    paidFriendsCount: int
    totalCashbackRub: int
    availableBalanceRub: int
    currentLevel: ReferralLevelInfo
    nextLevel: ReferralNextLevelInfo | None
    levels: list[ReferralLevelRow]
    pendingChests: list[ReferralPendingChest]
    lastRewards: list[ReferralRewardHistoryItem]
    totalBonusDays: int
    # Legacy numeric level for older clients. New UI uses currentLevel instead.
    level: int
    # Golden Period (PR3) — null when feature disabled or no active period.
    goldenPeriod: GoldenPeriodPayload | None = None


class ReferralChestRewardResponse(BaseModel):
    id: int
    levelKey: str
    levelName: str
    type: Literal["balance", "discount_percent"] | str
    value: int
    valueLabel: str
    title: str


class ReferralChestOpenResponse(BaseModel):
    reward: ReferralChestRewardResponse
    status: ReferralStatusResponse


@router.get("/status", response_model=ReferralStatusResponse)
async def get_status(user: Users = Depends(validate)) -> ReferralStatusResponse:
    payload = await build_referral_status(user)
    # Inject Golden Period state. None means feature off or no active period
    # — the FE conditionally renders the banner. Wrapped in try/except so the
    # Golden Period stack cannot break the existing referral status surface.
    try:
        from bloobcat.services.golden_period import build_golden_period_status

        gp_state = await build_golden_period_status(user)
        if gp_state is not None:
            payload["goldenPeriod"] = gp_state
    except Exception as exc:  # noqa: BLE001 - never break the parent endpoint
        logger.debug("golden_period_status_inject_failed user=%s err=%s", user.id, exc)
    return ReferralStatusResponse(**payload)


@router.post("/chests/{chest_id}/open", response_model=ReferralChestOpenResponse)
async def open_chest(
    chest_id: int,
    user: Users = Depends(validate),
) -> ReferralChestOpenResponse:
    reward = await open_referral_chest(user=user, chest_id=int(chest_id))
    if reward is None:
        raise HTTPException(status_code=404, detail="Referral chest not found or already opened")
    status = await build_referral_status(user, ensure_chests=True)
    return ReferralChestOpenResponse(
        reward=ReferralChestRewardResponse(**reward),
        status=ReferralStatusResponse(**status),
    )


@router.post("/invite")
async def log_invite(user: Users = Depends(validate)) -> Dict[str, Any]:
    logger.info(f"Referral invite created by user {user.id}")
    return {"ok": True}


# ── Home-screen install reward ──────────────────────────────────────────
# Frontend (HomeScreenInstallCard) calls this after the Telegram
# `homeScreenAdded` event fires. Idempotent: subsequent calls echo
# {already_claimed: true} without re-granting. Spec: ai_docs/develop/
# telegram-webapp-features-spec-2026-05-12.md (Variant A).


class HomeScreenClaimRequest(BaseModel):
    reward_kind: Literal["balance", "discount"] = Field(
        ..., description="balance = +50 ₽ to user balance, discount = 10% off next purchase"
    )
    platform_hint: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Diagnostic only (ios / android / web / tdesktop); not persisted",
    )


class HomeScreenClaimResponse(BaseModel):
    already_claimed: bool
    reward_kind: Optional[Literal["balance", "discount"]] = None
    amount: Optional[int] = None
    expires_at: Optional[str] = None


@router.post("/home-screen-claim", response_model=HomeScreenClaimResponse)
async def home_screen_claim(
    payload: HomeScreenClaimRequest,
    user: Users = Depends(validate),
) -> HomeScreenClaimResponse:
    result = await claim_home_screen_reward(
        user_id=int(user.id),
        reward_kind=payload.reward_kind,
        platform_hint=payload.platform_hint,
    )
    return HomeScreenClaimResponse(**result)


# ── Self-serve repair: "my bonus didn't arrive" ───────────────────────
# The frontend exposes this behind a CTA on `HomeScreenInstallCard` /
# `PwaInstalledRewardModal` when a user has the local "claimed" gate set
# but `/my-rewards` shows no install-discount and no recent balance
# bump. Calling this either confirms the bonus IS there (frontend
# reconciles its local gate) or detects an orphan and clears the flag
# so the user can retry the regular claim flow.
#
# Safety: clear-mode only. Credit-mode requires admin context
# (`/admin/integration/users/{id}/home-screen-reward/repair`) so an
# adversary can't spam this for free balance.


class HomeScreenRepairResponse(BaseModel):
    repaired: bool
    state: Literal[
        "no_claim", "consistent", "cleared_orphan_can_retry"
    ]
    has_install_discount: bool
    granted_at: Optional[str] = None


@router.post(
    "/home-screen-claim/repair", response_model=HomeScreenRepairResponse
)
async def home_screen_claim_repair(
    user: Users = Depends(validate),
) -> HomeScreenRepairResponse:
    user_id = int(user.id)
    orphans = await scan_home_screen_orphans(user_id=user_id, limit=1)
    if not orphans:
        # Two sub-cases: never claimed (flag NULL) vs claimed and consistent.
        granted = getattr(user, "home_screen_reward_granted_at", None)
        if granted is None:
            return HomeScreenRepairResponse(
                repaired=False,
                state="no_claim",
                has_install_discount=False,
                granted_at=None,
            )
        # Flag set and discount row exists (or balance variant — no
        # orphan possible for balance because the original UPDATE was
        # atomic). State is already consistent; frontend should re-fetch
        # /my-rewards to surface the actual reward.
        from bloobcat.db.discounts import PersonalDiscount

        has_discount = await PersonalDiscount.filter(
            user_id=user_id, source="home_screen_install"
        ).exists()
        return HomeScreenRepairResponse(
            repaired=False,
            state="consistent",
            has_install_discount=bool(has_discount),
            granted_at=granted.isoformat() if granted else None,
        )

    # Orphan confirmed: discount-kind claim with the flag set but no
    # PersonalDiscount row. Clear the flag so the user can retry.
    result = await repair_home_screen_reward(
        user_id=user_id, mode="clear", actor="self-serve",
    )
    logger.warning(
        "home-screen self-serve repair (clear): user=%s before=%s",
        user_id,
        result["before"],
    )
    return HomeScreenRepairResponse(
        repaired=True,
        state="cleared_orphan_can_retry",
        has_install_discount=False,
        granted_at=None,
    )


# ── Story-share payload ────────────────────────────────────────────────
# Returns the deterministic story code + deep-link + image URL the frontend
# hands to `WebApp.shareToStory`. Idempotent: the code is a pure function of
# the user_id + bot token, so repeated calls return the same code. The first
# call also materializes `users.story_code` so the registration consumer can
# resolve referrer in O(1).
#
# `image_url` points at the story-share image renderer (`/referrals/story-image`)
# — that endpoint is implemented in a follow-up PR alongside the Pillow
# overlay generator. Until then, the frontend can ship a static placeholder
# image baked into the bundle and substitute the live URL once the renderer
# ships. The `widget_link` and `code` are fully usable today.


class StorySharePayloadResponse(BaseModel):
    code: str = Field(..., description="Deterministic story-share code (STORY...)")
    widget_link: str = Field(
        ..., description="Telegram Mini App deep link with startapp=story_<code>"
    )
    image_url: str = Field(
        ...,
        description=(
            "HTTPS URL of the share image. Currently a placeholder until the Pillow "
            "renderer ships in the follow-up PR; frontend MUST treat this as opaque."
        ),
    )
    bonus_summary: str = Field(
        ...,
        description="Human-readable copy ('20 дней / 1 устройство / 1 GB LTE') for UI",
    )


@router.get("/story-share-payload", response_model=StorySharePayloadResponse)
async def story_share_payload(
    user: Users = Depends(validate),
) -> StorySharePayloadResponse:
    code = encode_story_code(int(user.id))
    # Persist for O(1) lookup at consume time. Safe to call repeatedly.
    await materialize_user_story_code(int(user.id))

    webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
    startapp = f"story_{code}"
    if webapp_url and webapp_url.lower().startswith("https://"):
        sep = "&" if "?" in webapp_url else "?"
        widget_link = f"{webapp_url.rstrip('/')}{sep}startapp={quote(startapp, safe='')}"
    else:
        try:
            bot_name = await get_bot_username()
        except Exception:
            bot_name = "VectraConnect_bot"
        widget_link = f"https://t.me/{bot_name}/start?startapp={quote(startapp, safe='')}"

    # Telegram's `shareToStory` fetches the image server-side. The frontend
    # `app.vectra-pro.net/api/...` proxy route adds duplicate response headers
    # and drops Content-Length on the binary stream, which makes Telegram
    # wait until its own fetch timeout — observable as an "infinite spinner"
    # on the share button. Issuing an absolute URL straight to the backend
    # API origin (api-app.vectra-pro.net) skips the proxy entirely so the
    # full JPG lands at the validator in <1s.
    api_base = (script_settings.api_url or "").rstrip("/")
    if api_base:
        image_url = f"{api_base}/referrals/story-image?code={quote(code, safe='')}"
    else:
        # Fallback for unconfigured envs (tests, local dev). The frontend's
        # `new URL(payload.image_url, window.location.origin)` resolves this
        # against the Mini App origin.
        image_url = f"/referrals/story-image?code={quote(code, safe='')}"

    return StorySharePayloadResponse(
        code=code,
        widget_link=widget_link,
        image_url=image_url,
        bonus_summary="20 дней Vectra + 1 устройство + 1 GB LTE",
    )


# Pillow-rendered JPG (1080x1920) for `WebApp.shareToStory`. The route is
# intentionally public — Telegram fetches the media from outside the user's
# auth session — but a code arriving here without HMAC structure validation
# returns 404 so the endpoint can never be turned into a generic image renderer.
@router.get("/story-image")
async def story_image(
    code: str = Query(..., min_length=1, max_length=64),
) -> Response:
    if len(code) != encoded_story_code_length():
        raise HTTPException(status_code=404, detail="not_found")
    # Structural well-formedness check via re-encode round-trip: any code that
    # isn't STORY+base32 will produce a different length / character set.
    if not code.startswith("STORY"):
        raise HTTPException(status_code=404, detail="not_found")

    try:
        payload = render_story_image(code)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("story-image render failed for code=%s: %s", code, exc)
        raise HTTPException(status_code=500, detail="render_failed") from exc

    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={
            # Edge-cacheable for a day. The renderer is deterministic so a stale
            # cache hit is harmless (same `code` always produces the same bytes).
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Disposition": 'inline; filename="vectra-story.jpg"',
            "X-Image-Dimensions": f"{STORY_IMAGE_WIDTH}x{STORY_IMAGE_HEIGHT}",
        },
    )


# ── My rewards: aggregate dashboard ─────────────────────────────────────
# Frontend renders /account/rewards from this single payload. Combines:
#   - Internal balance (RUB)
#   - Active PersonalDiscount rows (10% от home-screen install, promo codes,
#     prize wheel, admin grants, winback)
#   - Home-screen install reward state (claimed / pending / not eligible)
# Referral chests + cashback stay on /referrals to preserve the existing
# gamification surface — we just link to that page from /account/rewards.


class PersonalDiscountRow(BaseModel):
    id: int
    percent: int
    source: Optional[str] = None
    is_permanent: bool
    remaining_uses: int
    expires_at: Optional[str] = None
    min_months: Optional[int] = None
    max_months: Optional[int] = None
    label: str = Field(..., description="Human-readable summary for UI chip")


class HomeScreenRewardStatus(BaseModel):
    eligible: bool = Field(..., description="True if the install reward path is unlocked on this device")
    granted_at: Optional[str] = None
    reward_summary: Optional[str] = None


class MyRewardsResponse(BaseModel):
    balance_rub: int
    personal_discounts: list[PersonalDiscountRow]
    home_screen: HomeScreenRewardStatus
    referrals_link: str = Field(..., description="In-app path to the cashback gamification surface")


def _discount_source_label(source: Optional[str]) -> str:
    src = (source or "").lower()
    if src == "home_screen_install":
        return "Бонус за установку на главный экран"
    if src == "promo":
        return "Промокод"
    if src == "prize_wheel":
        return "Колесо призов"
    if src == "winback":
        return "Возврат подписки"
    if src == "admin":
        return "Подарок от админа"
    return "Скидка"


def _discount_label(row) -> str:
    base = _discount_source_label(getattr(row, "source", None))
    pct = int(getattr(row, "percent", 0) or 0)
    if pct <= 0:
        return base
    if getattr(row, "is_permanent", False):
        return f"{base}: −{pct}% (бессрочно)"
    uses = int(getattr(row, "remaining_uses", 0) or 0)
    if uses > 1:
        return f"{base}: −{pct}% (осталось {uses})"
    return f"{base}: −{pct}%"


@router.get("/my-rewards", response_model=MyRewardsResponse)
async def my_rewards(user: Users = Depends(validate)) -> MyRewardsResponse:
    from bloobcat.db.discounts import PersonalDiscount

    rows = await PersonalDiscount.filter(user_id=int(user.id)).order_by("-created_at").all()
    today = date.today()
    active: list[PersonalDiscountRow] = []
    for row in rows:
        expires = getattr(row, "expires_at", None)
        if expires and expires < today:
            continue
        remaining = int(getattr(row, "remaining_uses", 0) or 0)
        if not getattr(row, "is_permanent", False) and remaining <= 0:
            continue
        active.append(
            PersonalDiscountRow(
                id=int(row.id),
                percent=int(row.percent or 0),
                source=getattr(row, "source", None),
                is_permanent=bool(getattr(row, "is_permanent", False)),
                remaining_uses=remaining,
                expires_at=expires.isoformat() if expires else None,
                min_months=getattr(row, "min_months", None),
                max_months=getattr(row, "max_months", None),
                label=_discount_label(row),
            )
        )

    home_screen_added = getattr(user, "home_screen_added_at", None)
    home_screen_granted = getattr(user, "home_screen_reward_granted_at", None)
    if home_screen_granted is not None:
        hs = HomeScreenRewardStatus(
            eligible=False,
            granted_at=home_screen_granted.isoformat() if home_screen_granted else None,
            reward_summary="Бонус за установку на главный экран получен",
        )
    elif home_screen_added is not None:
        hs = HomeScreenRewardStatus(
            eligible=True,
            granted_at=None,
            reward_summary="Иконка добавлена — выбери бонус",
        )
    else:
        hs = HomeScreenRewardStatus(
            eligible=True,
            granted_at=None,
            reward_summary=None,
        )

    return MyRewardsResponse(
        balance_rub=int(getattr(user, "balance", 0) or 0),
        personal_discounts=active,
        home_screen=hs,
        referrals_link="/referrals",
    )


# Public constants exposed for callers that need to render copy without
# hitting the endpoint. Kept here (not on the service) so the API contract
# stays in one place.
__all__ = [
    "HOME_SCREEN_BALANCE_BONUS_RUB",
    "HOME_SCREEN_DISCOUNT_PERCENT",
    "HOME_SCREEN_DISCOUNT_TTL_DAYS",
]

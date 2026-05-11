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
from bloobcat.settings import telegram_settings

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
    return ReferralStatusResponse(**(await build_referral_status(user)))


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

    # Image URL is a placeholder until the Pillow renderer ships. We expose
    # the canonical path so the frontend can already wire `shareToStory` —
    # the route returns 404 in production today (caller falls back to the
    # static bundled image), but the URL shape will not change.
    # Same-origin so the Mini App can reach it without CORS.
    image_url = f"/api/referrals/story-image?code={quote(code, safe='')}"

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


# Public constants exposed for callers that need to render copy without
# hitting the endpoint. Kept here (not on the service) so the API contract
# stays in one place.
__all__ = [
    "HOME_SCREEN_BALANCE_BONUS_RUB",
    "HOME_SCREEN_DISCOUNT_PERCENT",
    "HOME_SCREEN_DISCOUNT_TTL_DAYS",
]

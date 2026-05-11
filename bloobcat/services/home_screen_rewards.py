"""Home-screen install reward — idempotent one-time bonus.

Spec (frozen 2026-05-12): when a user accepts Telegram's
"Add to home screen" prompt, the frontend calls
`POST /referrals/home-screen-claim` with the reward they picked
(`balance` for +50 ₽ to `Users.balance`, or `discount` for a 10%
one-shot personal discount on the next purchase). The endpoint is
idempotent — once `Users.home_screen_reward_granted_at` is non-null,
subsequent calls echo `{already_claimed: true}` without granting again.

Why a one-time chest instead of "+1 paid_friends_count":

The literal "+1 уровень" reading of the user's brief would bump
`paid_friends_count`, which permanently raises the user's referral
cashback tier — granting an unbounded lifetime reward for a one-time
action. Variant A from the spec (50 ₽ OR 10% discount, user picks) keeps
the bonus bounded while still delivering "felt" value at the install
moment.

Personal discounts (10% next-purchase) are stored as a `PersonalDiscount`
row with `source='home_screen_install'` and `remaining_uses=1`. The
balance variant is a straight `users.balance += 50`. Both writes happen
inside a single transaction with the timestamp flips, so a partial
failure can never leave the user with the bonus but no audit trail (or
vice versa).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal, TypedDict

from tortoise.transactions import in_transaction

from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("services.home_screen_rewards")

# Public constants — frontend mirrors these in its install card copy.
HOME_SCREEN_BALANCE_BONUS_RUB = 50
HOME_SCREEN_DISCOUNT_PERCENT = 10
HOME_SCREEN_DISCOUNT_TTL_DAYS = 90

HomeScreenRewardKind = Literal["balance", "discount"]


class HomeScreenClaimResult(TypedDict, total=False):
    already_claimed: bool
    reward_kind: HomeScreenRewardKind
    amount: int  # rub for 'balance', percent for 'discount'
    expires_at: str | None  # ISO date for 'discount', None for 'balance'


async def claim_home_screen_reward(
    user_id: int,
    reward_kind: HomeScreenRewardKind,
    *,
    platform_hint: str | None = None,
) -> HomeScreenClaimResult:
    """Grant the home-screen install bonus exactly once per user.

    `platform_hint` is logged but not stored — it is a "where the click
    came from" diagnostic (iOS/Android/web/tdesktop) useful for funnel
    debugging and not interesting after the reward lands.

    Idempotency guarantee: the row update is gated on
    `home_screen_reward_granted_at IS NULL` so two concurrent claims race
    to a single winner. The loser sees `{already_claimed: true}`.
    """
    if reward_kind not in ("balance", "discount"):
        raise ValueError(f"unknown reward_kind: {reward_kind!r}")

    now = datetime.now(timezone.utc)

    async with in_transaction() as conn:
        user = await Users.filter(id=int(user_id)).using_db(conn).first()
        if user is None:
            raise ValueError(f"user {user_id} not found")

        if user.home_screen_reward_granted_at is not None:
            return {"already_claimed": True}

        if reward_kind == "balance":
            # Single-row UPDATE so a concurrent caller cannot double-credit
            # the same balance bonus — the WHERE filters the timestamp
            # to NULL, so the second updater modifies zero rows.
            rows = (
                await Users.filter(
                    id=int(user_id), home_screen_reward_granted_at__isnull=True
                )
                .using_db(conn)
                .update(
                    balance=user.balance + HOME_SCREEN_BALANCE_BONUS_RUB,
                    home_screen_reward_granted_at=now,
                    home_screen_added_at=user.home_screen_added_at or now,
                )
            )
            if rows == 0:
                return {"already_claimed": True}
            logger.info(
                "home-screen reward (balance) granted: user=%s +%s RUB platform=%s",
                user_id,
                HOME_SCREEN_BALANCE_BONUS_RUB,
                platform_hint,
            )
            return {
                "already_claimed": False,
                "reward_kind": "balance",
                "amount": HOME_SCREEN_BALANCE_BONUS_RUB,
                "expires_at": None,
            }

        # reward_kind == "discount"
        expires_at: date = date.today() + timedelta(days=HOME_SCREEN_DISCOUNT_TTL_DAYS)
        rows = (
            await Users.filter(
                id=int(user_id), home_screen_reward_granted_at__isnull=True
            )
            .using_db(conn)
            .update(
                home_screen_reward_granted_at=now,
                home_screen_added_at=user.home_screen_added_at or now,
            )
        )
        if rows == 0:
            return {"already_claimed": True}

        await PersonalDiscount.create(
            using_db=conn,
            user_id=int(user_id),
            percent=HOME_SCREEN_DISCOUNT_PERCENT,
            is_permanent=False,
            remaining_uses=1,
            expires_at=expires_at,
            source="home_screen_install",
            metadata={"platform_hint": platform_hint} if platform_hint else {},
        )
        logger.info(
            "home-screen reward (discount) granted: user=%s %s%% TTL=%sd platform=%s",
            user_id,
            HOME_SCREEN_DISCOUNT_PERCENT,
            HOME_SCREEN_DISCOUNT_TTL_DAYS,
            platform_hint,
        )
        return {
            "already_claimed": False,
            "reward_kind": "discount",
            "amount": HOME_SCREEN_DISCOUNT_PERCENT,
            "expires_at": expires_at.isoformat(),
        }

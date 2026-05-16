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
balance variant is a straight `users.balance += 50`. The discount path
delivers the `PersonalDiscount` row BEFORE flipping
`home_screen_reward_granted_at` so a partial failure can never leave the
user with the flag set but no audit trail (the prior order let crashes
between UPDATE and create() lock the user out of the reward forever —
ICM 2026-05-12 daily-bug-scan: "code-evidence concurrency risks in
home_screen_rewards balance update and home_screen_install_promo
send-before-claim").
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, TypedDict

from tortoise.transactions import in_transaction

from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("services.home_screen_rewards")

# Public constants — frontend mirrors these in its install card copy.
HOME_SCREEN_BALANCE_BONUS_RUB = 50
HOME_SCREEN_DISCOUNT_PERCENT = 10
HOME_SCREEN_DISCOUNT_TTL_DAYS = 90

HomeScreenRewardKind = Literal["balance", "discount"]
HomeScreenRepairMode = Literal["clear", "credit"]
HomeScreenTrigger = Literal[
    "appinstalled",        # W3C event (Chrome/Edge desktop, Android Chrome)
    "first_standalone",    # iOS Safari, Android Yandex — first standalone open
    "pending_flag",        # cross-tab bridge after appinstalled in another tab
    "boot",                # repeat standalone open (weakest signal)
    "manual",              # "Я уже добавил иконку" escape hatch
    "unknown",             # legacy frontend that doesn't send trigger
]

_STRONG_TRIGGERS: frozenset[str] = frozenset(
    {"appinstalled", "first_standalone", "pending_flag"}
)


class HomeScreenClaimResult(TypedDict, total=False):
    already_claimed: bool
    reward_kind: HomeScreenRewardKind
    amount: int  # rub for 'balance', percent for 'discount'
    expires_at: str | None  # ISO date for 'discount', None for 'balance'
    verdict: str  # strong / weak / manual_with_push / manual_no_push / unknown


async def claim_home_screen_reward(
    user_id: int,
    reward_kind: HomeScreenRewardKind,
    *,
    platform_hint: str | None = None,
    trigger: HomeScreenTrigger | None = None,
) -> HomeScreenClaimResult:
    """Grant the home-screen install bonus exactly once per user.

    `platform_hint` (ios/android/web/tdesktop) and `trigger` (which
    detection signal opened the reward modal) are recorded in the
    `home_screen_install_signals` ledger for every call — including
    the duplicate `already_claimed` cases — so we can analyze the
    install-detection funnel and the manual-bypass rate post-fact.

    `trigger=None` is accepted for backward compatibility with frontends
    that haven't been updated yet; the signal is logged as `unknown`.

    Verdict policy (shadow mode in v1.99.0): the verdict is computed and
    stored but does NOT block the grant. Once we have a week of real
    data we will decide whether to convert `manual_no_push` into a hard
    `403 install_unverified`.

    Idempotency guarantee: the row update is gated on
    `home_screen_reward_granted_at IS NULL` so two concurrent claims race
    to a single winner. The loser sees `{already_claimed: true}`.

    Discount path is delivery-first: `PersonalDiscount.create()` runs
    before the flag flip, so a `create()` failure rolls back the whole
    transaction and the flag stays NULL (the user can retry).
    """
    if reward_kind not in ("balance", "discount"):
        raise ValueError(f"unknown reward_kind: {reward_kind!r}")

    now = datetime.now(timezone.utc)
    normalized_trigger = _normalize_trigger(trigger)
    had_active_push_sub = await _has_active_push_subscription(int(user_id))
    verdict = _compute_verdict(normalized_trigger, had_active_push_sub)

    async with in_transaction() as conn:
        user = await _fetch_user_locked(int(user_id), conn)
        if user is None:
            raise ValueError(f"user {user_id} not found")

        if user.home_screen_reward_granted_at is not None:
            await _record_signal(
                conn,
                user_id=int(user_id),
                trigger=normalized_trigger,
                platform_hint=platform_hint,
                reward_kind=reward_kind,
                had_active_push_sub=had_active_push_sub,
                verdict=verdict,
                already_claimed=True,
            )
            logger.info(
                "home-screen claim already-claimed (cache): user=%s kind=%s "
                "platform=%s trigger=%s verdict=%s push=%s",
                user_id,
                reward_kind,
                platform_hint,
                normalized_trigger,
                verdict,
                had_active_push_sub,
            )
            return {"already_claimed": True, "verdict": verdict}

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
                await _record_signal(
                    conn,
                    user_id=int(user_id),
                    trigger=normalized_trigger,
                    platform_hint=platform_hint,
                    reward_kind=reward_kind,
                    had_active_push_sub=had_active_push_sub,
                    verdict=verdict,
                    already_claimed=True,
                )
                logger.info(
                    "home-screen claim already-claimed (race): user=%s kind=balance "
                    "platform=%s trigger=%s verdict=%s",
                    user_id,
                    platform_hint,
                    normalized_trigger,
                    verdict,
                )
                return {"already_claimed": True, "verdict": verdict}
            await _record_signal(
                conn,
                user_id=int(user_id),
                trigger=normalized_trigger,
                platform_hint=platform_hint,
                reward_kind=reward_kind,
                had_active_push_sub=had_active_push_sub,
                verdict=verdict,
                already_claimed=False,
            )
            _log_grant(
                reward_kind="balance",
                user_id=user_id,
                platform_hint=platform_hint,
                trigger=normalized_trigger,
                verdict=verdict,
                had_active_push_sub=had_active_push_sub,
            )
            return {
                "already_claimed": False,
                "reward_kind": "balance",
                "amount": HOME_SCREEN_BALANCE_BONUS_RUB,
                "expires_at": None,
                "verdict": verdict,
            }

        # reward_kind == "discount" — deliver discount FIRST, set flag LAST.
        expires_at: date = date.today() + timedelta(days=HOME_SCREEN_DISCOUNT_TTL_DAYS)
        try:
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
        except Exception:
            logger.error(
                "home-screen claim discount-create failed: user=%s platform=%s "
                "trigger=%s — transaction rolled back",
                user_id,
                platform_hint,
                normalized_trigger,
                exc_info=True,
            )
            raise

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
            # Concurrent claim landed between our SELECT FOR UPDATE release
            # and the UPDATE — raise so the discount we just created rolls
            # back. The losing caller will see ValueError → 500 once; on
            # retry they hit the cache-path early return.
            logger.warning(
                "home-screen claim race after discount create: user=%s "
                "platform=%s trigger=%s — rolling back",
                user_id,
                platform_hint,
                normalized_trigger,
            )
            raise _ConcurrentClaimError(
                f"home-screen claim race after discount create for user {user_id}"
            )

        await _record_signal(
            conn,
            user_id=int(user_id),
            trigger=normalized_trigger,
            platform_hint=platform_hint,
            reward_kind=reward_kind,
            had_active_push_sub=had_active_push_sub,
            verdict=verdict,
            already_claimed=False,
        )
        _log_grant(
            reward_kind="discount",
            user_id=user_id,
            platform_hint=platform_hint,
            trigger=normalized_trigger,
            verdict=verdict,
            had_active_push_sub=had_active_push_sub,
        )
        return {
            "already_claimed": False,
            "reward_kind": "discount",
            "amount": HOME_SCREEN_DISCOUNT_PERCENT,
            "expires_at": expires_at.isoformat(),
            "verdict": verdict,
        }


def _normalize_trigger(trigger: HomeScreenTrigger | str | None) -> str:
    """Coerce caller-supplied trigger to a known string; fall back to 'unknown'."""
    if trigger is None:
        return "unknown"
    candidate = str(trigger).strip().lower()
    allowed = {
        "appinstalled",
        "first_standalone",
        "pending_flag",
        "boot",
        "manual",
        "unknown",
    }
    return candidate if candidate in allowed else "unknown"


def _compute_verdict(trigger: str, had_active_push_sub: bool) -> str:
    """Classify a claim signal for the install-funnel analytics.

    `strong` / `weak` / `manual_with_push` / `manual_no_push` are observed
    only — none of them block the grant in v1.99.0. The `manual_no_push`
    bucket is the candidate for a future hard-gate; v1.99.0 just logs
    a WARNING so we can sample its volume.
    """
    if trigger in _STRONG_TRIGGERS:
        return "strong"
    if trigger == "boot":
        return "weak"
    if trigger == "manual":
        return "manual_with_push" if had_active_push_sub else "manual_no_push"
    return "unknown"


async def _has_active_push_subscription(user_id: int) -> bool:
    """Return True if the user has at least one active web-push subscription.

    A push subscription can only be registered from a standalone PWA
    context (the SW must be installed), so an active row is a strong
    proxy for "this user has the app installed somewhere". A False
    result is NOT proof of "no install" (user may have declined push,
    cleared the subscription, or installed via Telegram Bot API which
    never registers a web-push subscription) — it's just one signal.
    """
    try:
        from bloobcat.db.push_subscriptions import PushSubscription

        return await PushSubscription.filter(
            user_id=int(user_id), is_active=True
        ).exists()
    except Exception:  # pragma: no cover — never break the claim flow
        logger.exception(
            "home-screen claim push-sub probe failed: user=%s", user_id
        )
        return False


async def _record_signal(
    conn: Any,
    *,
    user_id: int,
    trigger: str,
    platform_hint: str | None,
    reward_kind: HomeScreenRewardKind,
    had_active_push_sub: bool,
    verdict: str,
    already_claimed: bool,
) -> None:
    """Append an install-signal row to the ledger inside the active transaction."""
    try:
        await HomeScreenInstallSignal.create(
            using_db=conn,
            user_id=int(user_id),
            trigger=trigger,
            platform_hint=platform_hint,
            reward_kind=reward_kind,
            had_active_push_sub=had_active_push_sub,
            verdict=verdict,
            already_claimed=already_claimed,
        )
    except Exception:  # pragma: no cover — ledger never blocks reward
        logger.exception(
            "home-screen install-signal write failed: user=%s trigger=%s verdict=%s",
            user_id,
            trigger,
            verdict,
        )


def _log_grant(
    *,
    reward_kind: HomeScreenRewardKind,
    user_id: int,
    platform_hint: str | None,
    trigger: str,
    verdict: str,
    had_active_push_sub: bool,
) -> None:
    """Single log entry per successful grant — INFO normally, WARNING if suspicious."""
    log_fn = logger.warning if verdict == "manual_no_push" else logger.info
    if reward_kind == "balance":
        log_fn(
            "home-screen reward (balance) granted: user=%s +%s RUB "
            "platform=%s trigger=%s verdict=%s push=%s",
            user_id,
            HOME_SCREEN_BALANCE_BONUS_RUB,
            platform_hint,
            trigger,
            verdict,
            had_active_push_sub,
        )
    else:
        log_fn(
            "home-screen reward (discount) granted: user=%s %s%% TTL=%sd "
            "platform=%s trigger=%s verdict=%s push=%s",
            user_id,
            HOME_SCREEN_DISCOUNT_PERCENT,
            HOME_SCREEN_DISCOUNT_TTL_DAYS,
            platform_hint,
            trigger,
            verdict,
            had_active_push_sub,
        )


class _ConcurrentClaimError(RuntimeError):
    """Raised when the discount path detects a concurrent claim mid-transaction."""


async def _fetch_user_locked(user_id: int, conn: Any) -> Users | None:
    """Read the user row with FOR UPDATE on backends that support it.

    Tortoise's `.select_for_update()` is a PostgreSQL row lock; SQLite
    (used in tests) silently ignores it. Either way we get a fresh read
    inside the transaction, which is what idempotency relies on.
    """
    query = Users.filter(id=user_id).using_db(conn)
    try:
        locked_query = query.select_for_update()
        return await locked_query.first()
    except Exception:  # pragma: no cover — SQLite or stub backend
        return await query.first()


# ── Orphan-claim scan + repair (admin tooling) ─────────────────────────


class HomeScreenOrphanRow(TypedDict):
    user_id: int
    granted_at: str | None
    home_screen_added_at: str | None
    balance: int
    has_install_discount: bool
    likely_orphan: bool


async def scan_home_screen_orphans(
    *,
    user_id: int | None = None,
    limit: int | None = None,
    include_balance_suspects: bool = False,
) -> list[HomeScreenOrphanRow]:
    """Return users whose `home_screen_reward_granted_at` is set but who
    have no `PersonalDiscount(source='home_screen_install')` row.

    Balance-kind claims also have no PersonalDiscount row (the +50 ₽
    landed atomically in `users.balance`), so a strict
    "flag set + no discount" filter has false positives — balance-kind
    successes appear identical to discount-kind orphans because the
    chosen `reward_kind` isn't persisted on the user row.

    We compute `likely_orphan = True` only when `balance <`
    HOME_SCREEN_BALANCE_BONUS_RUB — the user has neither a discount row
    nor the +50 ₽ in their balance, so the bonus genuinely did not land.
    Balance-kind successes (balance ≥ 50, no discount) get
    `likely_orphan = False` and are filtered out of the default scan;
    pass `include_balance_suspects=True` to see them too.

    Pass `user_id` to scope the scan to a single user.
    """
    candidates_q = Users.filter(home_screen_reward_granted_at__isnull=False)
    if user_id is not None:
        candidates_q = candidates_q.filter(id=int(user_id))
    candidates_q = candidates_q.order_by("-home_screen_reward_granted_at")
    if limit is not None:
        candidates_q = candidates_q.limit(int(limit))

    candidates = await candidates_q.all()
    rows: list[HomeScreenOrphanRow] = []
    for user in candidates:
        has_discount = await PersonalDiscount.filter(
            user_id=int(user.id), source="home_screen_install"
        ).exists()
        if has_discount:
            continue
        balance = int(getattr(user, "balance", 0) or 0)
        likely_orphan = balance < HOME_SCREEN_BALANCE_BONUS_RUB
        if not likely_orphan and not include_balance_suspects:
            continue
        rows.append(
            HomeScreenOrphanRow(
                user_id=int(user.id),
                granted_at=(
                    user.home_screen_reward_granted_at.isoformat()
                    if user.home_screen_reward_granted_at
                    else None
                ),
                home_screen_added_at=(
                    user.home_screen_added_at.isoformat()
                    if user.home_screen_added_at
                    else None
                ),
                balance=balance,
                has_install_discount=False,
                likely_orphan=likely_orphan,
            )
        )
    return rows


class HomeScreenRepairResult(TypedDict):
    repaired: bool
    action: str
    before: dict[str, Any]
    after: dict[str, Any]


async def repair_home_screen_reward(
    user_id: int,
    mode: HomeScreenRepairMode,
    *,
    reward_kind: HomeScreenRewardKind | None = None,
    actor: str | None = None,
) -> HomeScreenRepairResult:
    """Admin repair for orphan / stuck home-screen reward state.

    Modes:

    * `clear` — set `home_screen_reward_granted_at = NULL` so the user
      can retry the claim from the app. Discount-kind orphans typically
      use this when we want the user to re-pick balance vs discount.
    * `credit` — explicitly grant the reward now. `reward_kind` is
      required. For discount-kind: creates a `PersonalDiscount` row only
      if one with `source='home_screen_install'` doesn't already exist
      (no-op if it does). For balance-kind: adds +50 ₽ unconditionally;
      callers MUST verify the balance wasn't credited already because
      there is no audit trail.

    Returns before/after snapshots for the admin UI / migration log.
    """
    if mode not in ("clear", "credit"):
        raise ValueError(f"unknown repair mode: {mode!r}")
    if mode == "credit" and reward_kind not in ("balance", "discount"):
        raise ValueError(f"credit mode requires reward_kind, got {reward_kind!r}")

    async with in_transaction() as conn:
        user = await Users.filter(id=int(user_id)).using_db(conn).first()
        if user is None:
            raise ValueError(f"user {user_id} not found")

        existing_discount = await PersonalDiscount.filter(
            user_id=int(user_id), source="home_screen_install"
        ).using_db(conn).first()

        before: dict[str, Any] = {
            "home_screen_reward_granted_at": (
                user.home_screen_reward_granted_at.isoformat()
                if user.home_screen_reward_granted_at
                else None
            ),
            "balance": int(getattr(user, "balance", 0) or 0),
            "has_install_discount": existing_discount is not None,
        }

        if mode == "clear":
            await Users.filter(id=int(user_id)).using_db(conn).update(
                home_screen_reward_granted_at=None,
            )
            after = {**before, "home_screen_reward_granted_at": None}
            logger.warning(
                "home-screen repair (clear): user=%s actor=%s before=%s",
                user_id,
                actor,
                before,
            )
            return {
                "repaired": True,
                "action": "cleared_flag",
                "before": before,
                "after": after,
            }

        # mode == "credit"
        now = datetime.now(timezone.utc)

        if reward_kind == "discount":
            if existing_discount is not None:
                # Discount already delivered — just ensure the flag matches.
                if user.home_screen_reward_granted_at is None:
                    await Users.filter(id=int(user_id)).using_db(conn).update(
                        home_screen_reward_granted_at=now,
                    )
                    logger.warning(
                        "home-screen repair (credit/discount): flag set to match existing discount user=%s actor=%s",
                        user_id,
                        actor,
                    )
                    after = {
                        **before,
                        "home_screen_reward_granted_at": now.isoformat(),
                    }
                    return {
                        "repaired": True,
                        "action": "flag_set_to_match_existing_discount",
                        "before": before,
                        "after": after,
                    }
                logger.info(
                    "home-screen repair (credit/discount): already consistent user=%s actor=%s",
                    user_id,
                    actor,
                )
                return {
                    "repaired": False,
                    "action": "already_consistent",
                    "before": before,
                    "after": before,
                }

            expires_at = date.today() + timedelta(days=HOME_SCREEN_DISCOUNT_TTL_DAYS)
            await PersonalDiscount.create(
                using_db=conn,
                user_id=int(user_id),
                percent=HOME_SCREEN_DISCOUNT_PERCENT,
                is_permanent=False,
                remaining_uses=1,
                expires_at=expires_at,
                source="home_screen_install",
                metadata={"repair": True, "actor": actor or "unknown"},
            )
            if user.home_screen_reward_granted_at is None:
                await Users.filter(id=int(user_id)).using_db(conn).update(
                    home_screen_reward_granted_at=now,
                )
            logger.warning(
                "home-screen repair (credit/discount): user=%s actor=%s before=%s",
                user_id,
                actor,
                before,
            )
            after = {
                **before,
                "home_screen_reward_granted_at": (
                    before["home_screen_reward_granted_at"] or now.isoformat()
                ),
                "has_install_discount": True,
            }
            return {
                "repaired": True,
                "action": "credited_discount",
                "before": before,
                "after": after,
            }

        # reward_kind == "balance" — caller-confirmed force-credit (no audit trail).
        new_balance = int(getattr(user, "balance", 0) or 0) + HOME_SCREEN_BALANCE_BONUS_RUB
        await Users.filter(id=int(user_id)).using_db(conn).update(
            balance=new_balance,
        )
        if user.home_screen_reward_granted_at is None:
            await Users.filter(id=int(user_id)).using_db(conn).update(
                home_screen_reward_granted_at=now,
            )
        logger.warning(
            "home-screen repair (credit/balance): user=%s actor=%s before=%s (no audit trail — caller confirmed)",
            user_id,
            actor,
            before,
        )
        after = {
            "home_screen_reward_granted_at": (
                before["home_screen_reward_granted_at"] or now.isoformat()
            ),
            "balance": new_balance,
            "has_install_discount": before["has_install_discount"],
        }
        return {
            "repaired": True,
            "action": "credited_balance",
            "before": before,
            "after": after,
        }

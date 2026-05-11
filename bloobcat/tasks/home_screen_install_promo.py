"""Scheduler task: 3-step decay push to add Vectra to home screen.

Spec: ai_docs/develop/telegram-webapp-features-spec-2026-05-12.md (section 6).
Cadence per user (each step gated on the previous attempt's age):

  - Attempt 1: 24 hours after registration_date
  - Attempt 2: 7 days after attempt 1
  - Attempt 3: 30 days after attempt 2
  - No more attempts after attempt 3 — the card on /account/security keeps
    handling organic claims forever.

Sending stops permanently once `home_screen_added_at IS NOT NULL` (user already
installed) regardless of which attempt they're on. Users with
`is_blocked=True` are excluded — they're not reachable on the bot side anyway.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from bloobcat.bot.notifications.general.home_screen_install_promo import (
    send_home_screen_install_promo,
)
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("tasks.home_screen_install_promo")

# Public so tests + scheduler imports stay in lockstep.
ATTEMPT_DELAYS_HOURS: tuple[int, ...] = (
    24,        # attempt 1: 24h after registration
    7 * 24,    # attempt 2: 7d after attempt 1
    30 * 24,   # attempt 3: 30d after attempt 2
)
MAX_ATTEMPTS = len(ATTEMPT_DELAYS_HOURS)
DEFAULT_SCAN_INTERVAL_SECONDS = 60 * 60  # 1h between scans


def _next_attempt_due_at(
    *,
    promo_sent_count: int,
    registration_date: datetime,
    last_sent_at: datetime | None,
) -> datetime | None:
    """Compute when the next attempt should fire for a given user.

    Returns None when the user has exhausted MAX_ATTEMPTS.
    """
    if promo_sent_count >= MAX_ATTEMPTS:
        return None
    delay_hours = ATTEMPT_DELAYS_HOURS[promo_sent_count]
    anchor = last_sent_at if promo_sent_count > 0 and last_sent_at else registration_date
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return anchor + timedelta(hours=delay_hours)


async def _process_user_if_due(user: Users, now: datetime) -> bool:
    """Returns True iff the bot push was actually sent."""
    promo_sent_count = int(getattr(user, "home_screen_promo_sent_count", 0) or 0)
    if promo_sent_count >= MAX_ATTEMPTS:
        return False
    if getattr(user, "home_screen_added_at", None) is not None:
        return False
    if getattr(user, "is_blocked", False):
        return False

    due_at = _next_attempt_due_at(
        promo_sent_count=promo_sent_count,
        registration_date=user.registration_date,
        last_sent_at=getattr(user, "home_screen_promo_sent_at", None),
    )
    if due_at is None or due_at > now:
        return False

    delivered = await send_home_screen_install_promo(user)
    if not delivered:
        return False

    # Persist the attempt — atomic UPDATE with the same NULL/count guard the
    # candidate filter uses, so concurrent runs cannot send twice.
    rows = await Users.filter(
        id=user.id,
        home_screen_added_at__isnull=True,
        home_screen_promo_sent_count=promo_sent_count,
    ).update(
        home_screen_promo_sent_at=now,
        home_screen_promo_sent_count=promo_sent_count + 1,
    )
    if rows == 0:
        # Another runner won the race — that's fine, exactly one message was
        # sent (theirs or ours; their concurrent send_message also bumped the
        # counter), and the next scan picks up the new state.
        return True
    logger.info(
        "home_screen_install_promo sent: user=%s attempt=%s/%s",
        user.id,
        promo_sent_count + 1,
        MAX_ATTEMPTS,
    )
    return True


async def _scan_once(now: datetime | None = None) -> int:
    """One pass over candidate users. Returns the number of messages sent."""
    if now is None:
        now = datetime.now(timezone.utc)
    sent = 0
    # Candidate filter keeps the scan cheap on the partial-index path:
    # `idx_users_home_screen_promo_pending` covers exactly this predicate.
    async for user in Users.filter(
        home_screen_added_at__isnull=True,
        home_screen_promo_sent_count__lt=MAX_ATTEMPTS,
        is_blocked=False,
        is_registered=True,
    ).only(
        "id",
        "full_name",
        "language_code",
        "registration_date",
        "home_screen_promo_sent_at",
        "home_screen_promo_sent_count",
        "home_screen_added_at",
        "is_blocked",
    ):
        try:
            if await _process_user_if_due(user, now):
                sent += 1
        except Exception as exc:  # pragma: no cover - defensive in scheduler loop
            logger.warning(
                "home_screen_install_promo process failed for user=%s: %s",
                user.id,
                exc,
            )
    return sent


async def run_home_screen_install_promo_scheduler(
    interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
) -> None:
    """Long-running scheduler task. Wakes up every `interval_seconds`, scans
    eligible users, and sends the appropriate push for each. Designed to be
    launched once at app start via `asyncio.create_task(...)`."""
    logger.info(
        "home_screen_install_promo scheduler started (interval=%ss)",
        interval_seconds,
    )
    while True:
        try:
            sent = await _scan_once()
            if sent > 0:
                logger.info("home_screen_install_promo scan: %s messages sent", sent)
        except Exception as exc:  # pragma: no cover - top-level guard
            logger.exception(
                "home_screen_install_promo scan crashed (will retry): %s", exc
            )
        await asyncio.sleep(interval_seconds)

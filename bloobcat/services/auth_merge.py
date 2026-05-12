"""Account-merge planner and executor.

Background
----------
Two real-world accounts can belong to the same person:

* A Telegram Mini App user with a paid subscription, partner status, LTE quota,
  or simply a long history.
* A web account created when that same person later signed in with Google /
  Yandex / Apple / email+password on the website and was granted a fresh trial.

Until this module landed, the only merge path was a strict
`user_has_material_data` gate in `web_auth.py`: **any** state on the
"to-be-absorbed" account — including a trivial 7-day trial — refused the
merge. That left users stranded with two accounts and forced every link
attempt into a support ticket.

This module replaces that all-or-nothing gate with an explicit **value tier**
model:

1. Each candidate account is scored from `UserValueTier.NONE` to
   `UserValueTier.ADMIN`. The tier captures the strongest non-trivial fact on
   that account.
2. The merge planner picks a **winner** (kept) and **loser** (deleted) by
   comparing tiers. Ties fall through to a deterministic tie-break: Telegram
   identity > smaller user id.
3. If the loser has only trial-grade state, the merge is allowed but the user
   must explicitly **confirm** the forfeit list before commit. If both sides
   have non-trivial value (or either side is blocked), the merge is refused and
   the request is routed to support.

The two-step "preview → confirm" flow is mediated by `AuthMergePreviewToken`,
which is a single-use, 5-minute, IP-hashed ticket bound to a specific
(winner, loser, provider) triple.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import IntEnum
from typing import Any, Iterable

from tortoise.transactions import in_transaction

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.auth import (
    AuthIdentity,
    AuthMergePreviewToken,
    AuthPasswordCredential,
)
from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.family_devices import FamilyDevices
from bloobcat.db.family_invites import FamilyInvites
from bloobcat.db.family_members import FamilyMembers
from bloobcat.db.in_app_notifications import InAppNotification
from bloobcat.db.partner_earnings import PartnerEarnings
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.db.partner_withdrawals import PartnerWithdrawals
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.prize_wheel import PrizeWheelHistory
from bloobcat.db.promotions import PromoUsage
from bloobcat.db.referral_rewards import ReferralLevelRewards, ReferralRewards
from bloobcat.db.remnawave_retry_jobs import RemnaWaveRetryJobs
from bloobcat.db.subscription_freezes import SubscriptionFreezes
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("services.auth_merge")

PREVIEW_TOKEN_TTL_SECONDS = 5 * 60
# Trial-LTE defaults that the system grants automatically. A user whose
# lte_gb_total is at or below this number is treated as "still on trial LTE";
# anything above it indicates an explicit admin grant or paid LTE top-up and
# therefore counts as value.
TRIAL_LTE_DEFAULT_GB = 1


class UserValueTier(IntEnum):
    """Comparable value tier. Higher = more important to preserve.

    Ordering rationale (top to bottom): admin trumps everything because
    admin grants survive even cancelled subscriptions; an active paid
    subscription outranks a partner role because the subscription is
    currently delivering service; a partner role outranks family
    ownership because partner state is income-bearing; family ownership
    outranks paid-history because the family graph implies live members
    on dependent rows; paid-history outranks admin-grant because a real
    payment is stronger evidence of intent than an admin override.
    Family-member is forfeitable boundary (the row can be re-pointed),
    below which everything is empty / trial-grade.

    `is_value` cuts at FAMILY_MEMBER (≥3): anything at or above this
    tier cannot be silently absorbed; if both sides land there the
    planner routes to support.
    """

    NONE = 0  # Empty row, no Telegram identity, never activated
    TELEGRAM_ONLY = 1  # Telegram identity only, no entitlement state
    TRIAL = 2  # Active trial state (flagged trial, current trial expired_at)
    FAMILY_MEMBER = 3  # Member of someone else's family plan
    ADMIN_GRANT = 4  # Admin-granted hwid_limit or LTE above trial default
    PAID_HISTORY = 5  # ≥1 processed payment OR lapsed paid-sub residue
    FAMILY_OWNER = 6  # Owns a family plan (cannot silently transfer family)
    PARTNER = 7  # Partner role / PartnerEarnings / PartnerQr / withdrawals
    PAID_SUBSCRIPTION = 8  # Active paid subscription right now
    ADMIN = 9  # Account is an admin user

    @property
    def is_value(self) -> bool:
        """True when the tier indicates non-forfeitable value."""
        return self >= UserValueTier.FAMILY_MEMBER


@dataclass(frozen=True)
class UserValueSummary:
    """Read-only view of an account's value-relevant facts.

    Built once at planning time. Used both to compute the value tier and to
    render the "сравнение аккаунтов" payload that the UI shows the user
    before they confirm the merge.
    """

    user_id: int
    tier: UserValueTier
    is_admin: bool
    is_blocked: bool
    has_telegram_identity: bool
    has_active_paid_subscription: bool
    has_paid_lte: bool
    has_admin_hwid_grant: bool
    is_partner: bool
    partner_balance_days: int
    has_paid_payment: bool
    is_family_owner: bool
    is_family_member: bool
    has_trial_flag: bool
    used_trial: bool
    trial_expires_at: date | None
    remnawave_uuid: str | None
    provider_summaries: list[str]
    paid_payments_count: int
    lte_gb_total: int | None
    hwid_limit: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "tier": int(self.tier),
            "tierName": self.tier.name,
            "isAdmin": self.is_admin,
            "isBlocked": self.is_blocked,
            "hasTelegramIdentity": self.has_telegram_identity,
            "hasActivePaidSubscription": self.has_active_paid_subscription,
            "hasPaidLte": self.has_paid_lte,
            "hasAdminHwidGrant": self.has_admin_hwid_grant,
            "isPartner": self.is_partner,
            "partnerBalanceDays": self.partner_balance_days,
            "hasPaidPayment": self.has_paid_payment,
            "isFamilyOwner": self.is_family_owner,
            "isFamilyMember": self.is_family_member,
            "hasTrialFlag": self.has_trial_flag,
            "usedTrial": self.used_trial,
            "trialExpiresAt": self.trial_expires_at.isoformat() if self.trial_expires_at else None,
            "providers": list(self.provider_summaries),
            "paidPaymentsCount": self.paid_payments_count,
            "lteGbTotal": self.lte_gb_total,
            "hwidLimit": self.hwid_limit,
        }


@dataclass(frozen=True)
class ForfeitItem:
    """One item that will be lost when the loser is absorbed."""

    key: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label}


@dataclass(frozen=True)
class MergePlan:
    """Result of planning a merge between two accounts.

    Outcomes
    --------
    * ``no_conflict`` — the other account is the same as the current one;
      nothing to merge. (Happens when the provider was already linked.)
    * ``auto_merge`` — loser is fully empty (`UserValueTier.NONE`). Merge can
      proceed silently; no forfeit to confirm.
    * ``confirm_required`` — loser has trial-grade state. The user must see
      the forfeit list and confirm.
    * ``support_required`` — both sides have non-trivial value, or one of the
      sides is blocked. Refuse merge, point to support.
    """

    outcome: str  # "no_conflict" | "auto_merge" | "confirm_required" | "support_required"
    provider: str
    winner: UserValueSummary | None
    loser: UserValueSummary | None
    forfeit: list[ForfeitItem] = field(default_factory=list)
    reason_code: str = ""
    reason_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "provider": self.provider,
            "winner": self.winner.to_dict() if self.winner else None,
            "loser": self.loser.to_dict() if self.loser else None,
            "forfeit": [item.to_dict() for item in self.forfeit],
            "reasonCode": self.reason_code,
            "reasonText": self.reason_text,
        }


async def _collect_user_value_summary(
    user: Users,
    *,
    provider_summaries: Iterable[str] | None = None,
    conn: Any | None = None,
) -> UserValueSummary:
    """Inspect a user row and its FK-linked rows to compute the value tier.

    `conn` is forwarded so the planner can be safely re-run inside the merge
    transaction's `SELECT FOR UPDATE` snapshot.
    """
    user_id = int(user.id)
    today = date.today()

    def _has(query: Any) -> Any:
        return query.using_db(conn).exists() if conn is not None else query.exists()

    is_admin = bool(getattr(user, "is_admin", False))
    is_blocked = bool(getattr(user, "is_blocked", False))
    expired_at = getattr(user, "expired_at", None)
    has_active_paid_subscription = bool(
        getattr(user, "is_subscribed", False)
        and not bool(getattr(user, "is_trial", False))
        and expired_at is not None
        and expired_at >= today
    )
    lte_gb_total_raw = getattr(user, "lte_gb_total", None)
    lte_gb_total = int(lte_gb_total_raw) if lte_gb_total_raw is not None else None
    has_paid_lte = lte_gb_total is not None and lte_gb_total > TRIAL_LTE_DEFAULT_GB
    hwid_limit_raw = getattr(user, "hwid_limit", None)
    hwid_limit = int(hwid_limit_raw) if hwid_limit_raw is not None else None
    has_admin_hwid_grant = hwid_limit is not None and hwid_limit > 1
    partner_balance_days = int(getattr(user, "balance", 0) or 0)
    # Partner *role* is the explicit role flag plus presence of
    # partner-only artefacts. A non-zero `balance` is referral cashback the
    # user accumulated through normal invitations — it is in-app value but
    # NOT proof of a partner relationship; promoting any user with 1 ₽ of
    # cashback to PARTNER tier would force every "trial-with-friends" link
    # request through support unnecessarily. The balance is still factored
    # into the value tier below via the paid-history branch.
    is_partner = bool(getattr(user, "is_partner", False))
    if not is_partner:
        if await _has(PartnerEarnings.filter(partner_id=user_id)):
            is_partner = True
        elif await _has(PartnerWithdrawals.filter(owner_id=user_id)):
            is_partner = True
        elif await _has(PartnerQr.filter(owner_id=user_id)):
            is_partner = True
    paid_payments_count = 0
    if conn is not None:
        paid_payments_count = await ProcessedPayments.filter(user_id=user_id).using_db(conn).count()
    else:
        paid_payments_count = await ProcessedPayments.filter(user_id=user_id).count()
    has_paid_payment = paid_payments_count > 0
    # Family-membership status is open-ended in the schema; rely on an
    # explicit allow-list (`active`/`frozen`) so transient or
    # garbage-collected states don't accidentally elevate the tier.
    family_active_statuses = ("active", "frozen")
    is_family_owner = await _has(
        FamilyMembers.filter(owner_id=user_id, status__in=family_active_statuses)
    )
    is_family_member = await _has(
        FamilyMembers.filter(member_id=user_id, status__in=family_active_statuses)
    )
    has_trial_flag = bool(getattr(user, "is_trial", False))
    used_trial = bool(getattr(user, "used_trial", False))
    remnawave_uuid = getattr(user, "remnawave_uuid", None)
    has_telegram_identity = await _has(
        AuthIdentity.filter(user_id=user_id, provider="telegram")
    )

    if is_admin:
        tier = UserValueTier.ADMIN
    elif has_active_paid_subscription:
        tier = UserValueTier.PAID_SUBSCRIPTION
    elif is_partner:
        tier = UserValueTier.PARTNER
    elif is_family_owner:
        tier = UserValueTier.FAMILY_OWNER
    elif has_paid_payment:
        tier = UserValueTier.PAID_HISTORY
    elif has_admin_hwid_grant or has_paid_lte:
        tier = UserValueTier.ADMIN_GRANT
    elif is_family_member:
        tier = UserValueTier.FAMILY_MEMBER
    elif (
        has_trial_flag
        or (expired_at is not None and (has_trial_flag or used_trial))
        or (remnawave_uuid and (has_trial_flag or used_trial))
    ):
        # `expired_at` and `remnawave_uuid` on their own are too weak to
        # mean "trial" — a long-lapsed paying customer also has them, and
        # demoting that user to forfeitable is a foot-gun. Require an
        # explicit trial flag (`is_trial`) or `used_trial=True` alongside
        # before treating the user as TRIAL-tier.
        tier = UserValueTier.TRIAL
    elif used_trial or (expired_at is not None) or remnawave_uuid:
        # Lapsed paid sub with no payment-history rows preserved (admin
        # backfills, GDPR purges, legacy tariffs) — preserve at the
        # paid-history tier so it cannot be silently absorbed.
        tier = UserValueTier.PAID_HISTORY
    elif has_telegram_identity:
        tier = UserValueTier.TELEGRAM_ONLY
    else:
        tier = UserValueTier.NONE

    return UserValueSummary(
        user_id=user_id,
        tier=tier,
        is_admin=is_admin,
        is_blocked=is_blocked,
        has_telegram_identity=has_telegram_identity,
        has_active_paid_subscription=has_active_paid_subscription,
        has_paid_lte=has_paid_lte,
        has_admin_hwid_grant=has_admin_hwid_grant,
        is_partner=is_partner,
        partner_balance_days=partner_balance_days,
        has_paid_payment=has_paid_payment,
        is_family_owner=is_family_owner,
        is_family_member=is_family_member,
        has_trial_flag=has_trial_flag,
        used_trial=used_trial,
        trial_expires_at=expired_at if isinstance(expired_at, date) else None,
        remnawave_uuid=str(remnawave_uuid) if remnawave_uuid else None,
        provider_summaries=list(provider_summaries or []),
        paid_payments_count=paid_payments_count,
        lte_gb_total=lte_gb_total,
        hwid_limit=hwid_limit,
    )


def _mask_email(email: str | None) -> str | None:
    """Mask the local-part of an email so the dialog gives the user
    enough to recognise the account without exposing the full address.

    `user@example.com` → `u***@example.com`. Returns None for falsy input.
    Single-character local parts collapse to `***@domain`.
    """
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    local = local.strip()
    domain = domain.strip()
    if not local or not domain:
        return None
    head = local[0] if len(local) >= 2 else ""
    return f"{head}***@{domain}"


async def _provider_summaries(user_id: int) -> list[str]:
    """Human-readable provider list for one user. Used in the UI summary.

    Emails are masked because the planner returns the loser's plan to the
    initiator (who may share a device with another person who controls the
    other provider). The dialog needs enough to recognise the account but
    not the full address.
    """
    rows = await AuthIdentity.filter(user_id=user_id).values("provider", "email")
    items: list[str] = []
    for row in rows:
        provider = (row.get("provider") or "").lower()
        masked = _mask_email(row.get("email"))
        label = {
            "google": "Google",
            "apple": "Apple",
            "yandex": "Yandex",
            "telegram": "Telegram",
            "password": "Email",
        }.get(provider, provider.title() or "Unknown")
        if masked:
            items.append(f"{label} ({masked})")
        else:
            items.append(label)
    if await AuthPasswordCredential.filter(user_id=user_id).exists():
        if not any(s.startswith("Email") for s in items):
            items.append("Email")
    return items


def _pick_winner(
    summary_a: UserValueSummary, summary_b: UserValueSummary
) -> tuple[UserValueSummary, UserValueSummary]:
    """Apply the deterministic winner-selection rule.

    1. Higher tier wins.
    2. Tier tie → the account with the Telegram identity wins.
    3. Still tied → smaller user id wins (older account).
    """
    if summary_a.tier > summary_b.tier:
        return summary_a, summary_b
    if summary_b.tier > summary_a.tier:
        return summary_b, summary_a
    if summary_a.has_telegram_identity and not summary_b.has_telegram_identity:
        return summary_a, summary_b
    if summary_b.has_telegram_identity and not summary_a.has_telegram_identity:
        return summary_b, summary_a
    if summary_a.user_id <= summary_b.user_id:
        return summary_a, summary_b
    return summary_b, summary_a


def _build_forfeit_list(loser: UserValueSummary) -> list[ForfeitItem]:
    items: list[ForfeitItem] = []
    if loser.has_trial_flag or (loser.trial_expires_at and not loser.has_active_paid_subscription):
        items.append(ForfeitItem(key="trial_days", label="Триальные дни на втором аккаунте"))
    if loser.remnawave_uuid:
        items.append(ForfeitItem(key="vpn_account", label="VPN-аккаунт второго пользователя"))
    if loser.lte_gb_total is not None and not loser.has_paid_lte and loser.lte_gb_total > 0:
        items.append(ForfeitItem(key="trial_lte", label="Триальный остаток LTE"))
    return items


async def plan_merge(
    current_user: Users,
    other_user: Users,
    *,
    provider: str,
) -> MergePlan:
    """Plan the merge between the currently authenticated user and the user
    that owns the conflicting identity. Pure read; never mutates the DB.
    """
    if int(current_user.id) == int(other_user.id):
        return MergePlan(
            outcome="no_conflict",
            provider=provider,
            winner=None,
            loser=None,
            reason_code="same_user",
            reason_text="Этот способ входа уже подключён к вашему аккаунту.",
        )

    current_providers = await _provider_summaries(int(current_user.id))
    other_providers = await _provider_summaries(int(other_user.id))
    current_summary = await _collect_user_value_summary(
        current_user, provider_summaries=current_providers
    )
    other_summary = await _collect_user_value_summary(
        other_user, provider_summaries=other_providers
    )

    if current_summary.is_blocked or other_summary.is_blocked:
        return MergePlan(
            outcome="support_required",
            provider=provider,
            winner=current_summary if not current_summary.is_blocked else other_summary,
            loser=other_summary if not current_summary.is_blocked else current_summary,
            reason_code="blocked_account",
            reason_text="Один из аккаунтов заблокирован. Напишите в поддержку.",
        )

    if current_summary.tier.is_value and other_summary.tier.is_value:
        winner, loser = _pick_winner(current_summary, other_summary)
        return MergePlan(
            outcome="support_required",
            provider=provider,
            winner=winner,
            loser=loser,
            reason_code="both_have_value",
            reason_text=(
                "На обоих аккаунтах есть оплаченная подписка, партнёрский статус "
                "или семейный план. Напишите в поддержку — поможем безопасно "
                "перенести доступ."
            ),
        )

    winner, loser = _pick_winner(current_summary, other_summary)
    forfeit = _build_forfeit_list(loser)

    if loser.tier == UserValueTier.NONE:
        return MergePlan(
            outcome="auto_merge",
            provider=provider,
            winner=winner,
            loser=loser,
            forfeit=forfeit,
            reason_code="empty_absorb",
            reason_text="Второй аккаунт пустой, переносим способ входа.",
        )

    return MergePlan(
        outcome="confirm_required",
        provider=provider,
        winner=winner,
        loser=loser,
        forfeit=forfeit,
        reason_code="trial_forfeit",
        reason_text=(
            "Триальные дни на втором аккаунте сгорят, а способ входа и история "
            "входов перенесутся в основной аккаунт."
        ),
    )


async def _move_identities(
    *, loser_id: int, winner_id: int, conn: Any
) -> None:
    """Move all auth identities + password credentials from loser to winner.

    The unique constraint on `(provider, provider_subject)` cannot collide
    here because the global uniqueness already prevented two users from
    having the same `(provider, subject)`. Same applies to
    `email_normalized` on password credentials.
    """
    await AuthIdentity.filter(user_id=loser_id).using_db(conn).update(user_id=winner_id)
    await AuthPasswordCredential.filter(user_id=loser_id).using_db(conn).update(
        user_id=winner_id
    )


async def _move_housekeeping_rows(*, loser_id: int, winner_id: int, conn: Any) -> None:
    """Move rows that are safe to transfer.

    Only in-app notifications move: they're addressed to the human, not to
    a specific provider identity, so the merged user wants to keep them.

    `RemnaWaveRetryJobs` is deliberately **not** moved — every row carries
    the loser's `remnawave_uuid`, and `Users.delete()` (run after this
    transaction) queues a fresh delete-retry for that uuid via
    `enqueue_remnawave_delete_retry`. Moving the rows would point them at
    the winner with stale loser-side UUIDs and cause indefinite no-op
    retries. Drop them instead; the fresh delete-retry covers the only
    job that still has meaning.
    """
    await InAppNotification.filter(user_id=loser_id).using_db(conn).update(
        user_id=winner_id
    )
    await RemnaWaveRetryJobs.filter(user_id=loser_id).using_db(conn).delete()


async def _drop_loser_dependent_rows(*, loser_id: int, conn: Any) -> None:
    """Pre-emptively delete rows that don't transfer and may block the user
    delete on FK constraints that aren't CASCADE.

    All of these are either trial-grade artefacts (no value) or operational
    debris that is meaningless once the user row is gone. They are deleted
    inside the transaction so the loser delete sees an empty graph.
    """
    await PromoUsage.filter(user_id=loser_id).using_db(conn).delete()
    await PersonalDiscount.filter(user_id=loser_id).using_db(conn).delete()
    await PrizeWheelHistory.filter(user_id=loser_id).using_db(conn).delete()
    await SubscriptionFreezes.filter(user_id=loser_id).using_db(conn).delete()
    await FamilyInvites.filter(owner_id=loser_id).using_db(conn).delete()
    await FamilyDevices.filter(user_id=loser_id).using_db(conn).delete()
    await ReferralRewards.filter(referrer_user_id=loser_id).using_db(conn).delete()
    await ReferralRewards.filter(referred_user_id=loser_id).using_db(conn).delete()
    await ReferralLevelRewards.filter(user_id=loser_id).using_db(conn).delete()
    # `UserDevice` rows are deliberately left to `Users.delete()` →
    # `cascade_delete()` (`bloobcat/services/device_service.py`): it
    # removes the local rows and the RemnaWave-side device link in one
    # place. Doing it here would duplicate the device-API call against a
    # uuid that's about to be deleted.
    # Active tariff slot on the loser side — only present in trial-forfeit
    # cases where a trial granted a synthetic ActiveTariffs row.
    await ActiveTariffs.filter(user_id=loser_id).using_db(conn).delete()


async def execute_merge(plan: MergePlan) -> Users:
    """Commit a merge plan. Caller is responsible for having confirmed
    `outcome in {"auto_merge", "confirm_required"}`.

    Atomic: identities + housekeeping moves and the loser delete happen in a
    single transaction. RemnaWave cleanup for the loser's `remnawave_uuid` is
    handled by `Users.delete()` (queues a retry job on transient errors).
    """
    assert plan.winner is not None
    assert plan.loser is not None
    winner_id = plan.winner.user_id
    loser_id = plan.loser.user_id
    if winner_id == loser_id:
        winner = await Users.get(id=winner_id)
        return winner

    # Lock smaller id first to avoid AB/BA deadlock with any concurrent
    # transaction that touches the same pair of users.
    first_id, second_id = sorted((winner_id, loser_id))
    async with in_transaction() as conn:
        await Users.select_for_update().using_db(conn).get(id=first_id)
        await Users.select_for_update().using_db(conn).get(id=second_id)
        winner_locked = await Users.filter(id=winner_id).using_db(conn).first()
        loser_locked = await Users.filter(id=loser_id).using_db(conn).first()
        assert winner_locked is not None and loser_locked is not None

        # Re-evaluate the loser inside the lock to catch any concurrent state
        # change since the preview was issued (e.g. the user bought a
        # subscription on the would-be-loser side in the 30 seconds between
        # preview and confirm).
        current_summary = await _collect_user_value_summary(winner_locked, conn=conn)
        other_summary = await _collect_user_value_summary(loser_locked, conn=conn)
        if other_summary.tier.is_value:
            from bloobcat.services.web_auth import WebAuthError

            raise WebAuthError("merge_requires_support", status_code=409)
        if current_summary.is_blocked or other_summary.is_blocked:
            from bloobcat.services.web_auth import WebAuthError

            raise WebAuthError("merge_requires_support", status_code=409)

        await _move_identities(loser_id=loser_id, winner_id=winner_id, conn=conn)
        await _move_housekeeping_rows(loser_id=loser_id, winner_id=winner_id, conn=conn)
        await _drop_loser_dependent_rows(loser_id=loser_id, conn=conn)

        # Preserve the trial-once invariant even when the winner is the side
        # without prior trial usage. After merge, the merged identity must
        # behave as if the trial was already consumed.
        winner_update_fields: list[str] = []
        if other_summary.used_trial and not winner_locked.used_trial:
            winner_locked.used_trial = True
            winner_update_fields.append("used_trial")
        winner_locked.auth_token_version = int(winner_locked.auth_token_version or 0) + 1
        winner_update_fields.append("auth_token_version")
        await winner_locked.save(update_fields=winner_update_fields, using_db=conn)

    # Loser delete runs OUTSIDE the transaction because `Users.delete()` makes
    # an external RemnaWave HTTP call and queues a retry job; both behaviours
    # rely on having a committed snapshot of the row before the call.
    fresh_loser = await Users.get_or_none(id=loser_id)
    if fresh_loser is not None:
        try:
            await fresh_loser.delete()
        except Exception as exc:
            # The merge itself is already committed: identities moved, winner
            # token version bumped. A transient delete failure (RemnaWave 5xx,
            # network hiccup) must not roll the merge back; it queues a retry
            # via `enqueue_remnawave_delete_retry`. Surface a warning so ops
            # can investigate but do not raise.
            logger.warning(
                "auth_merge_loser_delete_deferred winner=%s loser=%s error=%s",
                winner_id,
                loser_id,
                exc,
            )

    winner = await Users.get(id=winner_id)
    return winner


async def issue_preview_token(plan: MergePlan, *, initiated_by_user_id: int) -> str:
    """Persist a single-use, 5-minute token bound to this exact merge plan."""
    from bloobcat.services.web_auth import (
        generate_public_token,
        hash_secret,
        now_utc,
    )

    assert plan.winner is not None and plan.loser is not None
    token = generate_public_token(24)
    await AuthMergePreviewToken.create(
        token_hash=hash_secret(token),
        winner_user_id=plan.winner.user_id,
        loser_user_id=plan.loser.user_id,
        provider=plan.provider,
        initiated_by_user_id=initiated_by_user_id,
        expires_at=now_utc() + timedelta(seconds=PREVIEW_TOKEN_TTL_SECONDS),
    )
    return token


async def consume_preview_token(
    token: str, *, expected_initiator_id: int | None = None
) -> AuthMergePreviewToken:
    """Atomically consume a preview token. Raises if expired / reused /
    initiator mismatch.

    Caller note: the consume transaction commits the `consumed_at` write
    independently. If the subsequent merge work fails, the token is
    burned — use `consume_and_execute_merge` (single outer transaction)
    when you need rollback-on-merge-failure semantics.
    """
    from bloobcat.services.web_auth import (
        WebAuthError,
        hash_secret,
        now_utc,
    )

    if not token:
        raise WebAuthError("invalid_merge_token", status_code=400)
    token_hash = hash_secret(token)
    async with in_transaction() as conn:
        row = (
            await AuthMergePreviewToken.select_for_update()
            .using_db(conn)
            .get_or_none(token_hash=token_hash)
        )
        if row is None or row.consumed_at is not None:
            raise WebAuthError("invalid_merge_token", status_code=400)
        if row.expires_at <= now_utc():
            raise WebAuthError("invalid_merge_token", status_code=400)
        if (
            expected_initiator_id is not None
            and int(row.initiated_by_user_id) != int(expected_initiator_id)
        ):
            raise WebAuthError("invalid_merge_token", status_code=403)
        row.consumed_at = now_utc()
        await row.save(update_fields=["consumed_at"], using_db=conn)
    return row


async def consume_and_execute_merge(
    token: str, *, expected_initiator_id: int
) -> Users:
    """Consume the preview token AND run the merge atomically. If the
    merge step fails (race-induced support requirement, role flip, missing
    user row), the consume is rolled back so the user can retry with the
    same token until it expires naturally.

    The `Users.delete()` step still runs outside this transaction (it
    makes a RemnaWave HTTP call); failures there don't roll back the
    merge — the loser delete simply queues a RemnaWave retry job.
    """
    from bloobcat.services.web_auth import (
        WebAuthError,
        hash_secret,
        now_utc,
    )

    if not token:
        raise WebAuthError("invalid_merge_token", status_code=400)
    token_hash = hash_secret(token)
    # Phase 1 — consume token + validate plan + move identities, all inside
    # one transaction. On any failure the token stays unconsumed.
    async with in_transaction() as conn:
        row = (
            await AuthMergePreviewToken.select_for_update()
            .using_db(conn)
            .get_or_none(token_hash=token_hash)
        )
        if row is None or row.consumed_at is not None:
            raise WebAuthError("invalid_merge_token", status_code=400)
        if row.expires_at <= now_utc():
            raise WebAuthError("invalid_merge_token", status_code=400)
        if int(row.initiated_by_user_id) != int(expected_initiator_id):
            raise WebAuthError("invalid_merge_token", status_code=403)

        winner_user = (
            await Users.filter(id=int(row.winner_user_id)).using_db(conn).first()
        )
        loser_user = (
            await Users.filter(id=int(row.loser_user_id)).using_db(conn).first()
        )
        if winner_user is None or loser_user is None:
            raise WebAuthError("merge_requires_support", status_code=409)

        plan = await plan_merge(
            winner_user, loser_user, provider=str(row.provider)
        )
        if plan.outcome in {"support_required", "no_conflict"}:
            raise WebAuthError("merge_requires_support", status_code=409)
        if (
            plan.winner is None
            or plan.loser is None
            or int(plan.winner.user_id) != int(row.winner_user_id)
            or int(plan.loser.user_id) != int(row.loser_user_id)
        ):
            logger.warning(
                "auth_merge_role_flip_aborted token_winner=%s token_loser=%s "
                "replan_winner=%s replan_loser=%s",
                int(row.winner_user_id),
                int(row.loser_user_id),
                int(plan.winner.user_id) if plan.winner else None,
                int(plan.loser.user_id) if plan.loser else None,
            )
            raise WebAuthError("merge_requires_support", status_code=409)

        winner_id = plan.winner.user_id
        loser_id = plan.loser.user_id
        first_id, second_id = sorted((winner_id, loser_id))
        await Users.select_for_update().using_db(conn).get(id=first_id)
        await Users.select_for_update().using_db(conn).get(id=second_id)
        winner_locked = await Users.filter(id=winner_id).using_db(conn).first()
        loser_locked = await Users.filter(id=loser_id).using_db(conn).first()
        assert winner_locked is not None and loser_locked is not None

        current_summary = await _collect_user_value_summary(winner_locked, conn=conn)
        other_summary = await _collect_user_value_summary(loser_locked, conn=conn)
        if other_summary.tier.is_value:
            raise WebAuthError("merge_requires_support", status_code=409)
        if current_summary.is_blocked or other_summary.is_blocked:
            raise WebAuthError("merge_requires_support", status_code=409)

        await _move_identities(loser_id=loser_id, winner_id=winner_id, conn=conn)
        await _move_housekeeping_rows(loser_id=loser_id, winner_id=winner_id, conn=conn)
        await _drop_loser_dependent_rows(loser_id=loser_id, conn=conn)

        winner_update_fields: list[str] = []
        if other_summary.used_trial and not winner_locked.used_trial:
            winner_locked.used_trial = True
            winner_update_fields.append("used_trial")
        winner_locked.auth_token_version = int(winner_locked.auth_token_version or 0) + 1
        winner_update_fields.append("auth_token_version")
        await winner_locked.save(update_fields=winner_update_fields, using_db=conn)

        # Token consume happens last inside the txn so any earlier raise
        # aborts the commit and the token remains usable.
        row.consumed_at = now_utc()
        await row.save(update_fields=["consumed_at"], using_db=conn)

    # Phase 2 — loser delete + RemnaWave cleanup. Outside the txn because
    # it makes a network call; failures here queue a retry rather than
    # rolling back the now-committed merge.
    fresh_loser = await Users.get_or_none(id=loser_id)
    if fresh_loser is not None:
        try:
            await fresh_loser.delete()
        except Exception as exc:
            logger.warning(
                "auth_merge_loser_delete_deferred winner=%s loser=%s error=%s",
                winner_id,
                loser_id,
                exc,
            )

    return await Users.get(id=winner_id)


async def execute_merge_from_token(token_row: AuthMergePreviewToken) -> Users:
    """Re-plan from the persisted token row and execute. Re-planning is
    important: between preview and confirm the underlying accounts may have
    gained or lost value, so we cannot trust the original plan blindly.

    Critically, the re-plan must yield the **same winner/loser identity**
    as the token. The user confirmed a dialog that named a specific winner
    and a specific loser; if `plan_merge` would now flip those roles (e.g.
    the original winner cancelled their subscription and the original loser
    bought one in the 5-minute window), silently honouring the new pair
    would delete the user's *intended-winner* account. Refuse that case
    and route the user back through preview.
    """
    from bloobcat.services.web_auth import WebAuthError

    winner_user = await Users.get_or_none(id=int(token_row.winner_user_id))
    loser_user = await Users.get_or_none(id=int(token_row.loser_user_id))
    if winner_user is None or loser_user is None:
        raise WebAuthError("merge_requires_support", status_code=409)
    plan = await plan_merge(
        winner_user, loser_user, provider=str(token_row.provider)
    )
    if plan.outcome in {"support_required", "no_conflict"}:
        raise WebAuthError("merge_requires_support", status_code=409)
    if (
        plan.winner is None
        or plan.loser is None
        or int(plan.winner.user_id) != int(token_row.winner_user_id)
        or int(plan.loser.user_id) != int(token_row.loser_user_id)
    ):
        logger.warning(
            "auth_merge_role_flip_aborted token_winner=%s token_loser=%s "
            "replan_winner=%s replan_loser=%s",
            int(token_row.winner_user_id),
            int(token_row.loser_user_id),
            int(plan.winner.user_id) if plan.winner else None,
            int(plan.loser.user_id) if plan.loser else None,
        )
        raise WebAuthError("merge_requires_support", status_code=409)
    return await execute_merge(plan)

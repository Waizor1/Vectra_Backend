"""Unit tests for the account-merge planner.

These tests cover the pure decision logic (`_pick_winner`, `_build_forfeit_list`,
`plan_merge`, `start_link_or_merge`) by stubbing the value-summary collector
so we don't need a real database. The planner's outputs drive the most
sensitive product behaviour in the merge flow (which account survives, what
gets forfeited, when support is required) and so each branch is exercised
explicitly.
"""

from __future__ import annotations

import types

import pytest

from bloobcat.services import auth_merge, web_auth


def _summary(
    user_id: int,
    *,
    tier: auth_merge.UserValueTier = auth_merge.UserValueTier.NONE,
    is_admin: bool = False,
    is_blocked: bool = False,
    has_telegram_identity: bool = False,
    has_active_paid_subscription: bool = False,
    has_paid_lte: bool = False,
    has_admin_hwid_grant: bool = False,
    is_partner: bool = False,
    partner_balance_days: int = 0,
    has_paid_payment: bool = False,
    is_family_owner: bool = False,
    is_family_member: bool = False,
    has_trial_flag: bool = False,
    used_trial: bool = False,
    trial_expires_at=None,
    remnawave_uuid: str | None = None,
    provider_summaries=(),
    paid_payments_count: int = 0,
    lte_gb_total: int | None = None,
    hwid_limit: int | None = None,
) -> auth_merge.UserValueSummary:
    return auth_merge.UserValueSummary(
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
        trial_expires_at=trial_expires_at,
        remnawave_uuid=remnawave_uuid,
        provider_summaries=list(provider_summaries),
        paid_payments_count=paid_payments_count,
        lte_gb_total=lte_gb_total,
        hwid_limit=hwid_limit,
    )


def test_pick_winner_prefers_higher_tier():
    paid = _summary(100, tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION)
    trial = _summary(200, tier=auth_merge.UserValueTier.TRIAL)
    winner, loser = auth_merge._pick_winner(paid, trial)
    assert winner.user_id == 100
    assert loser.user_id == 200


def test_pick_winner_tier_tie_prefers_telegram_identity():
    a = _summary(900, tier=auth_merge.UserValueTier.TRIAL, has_telegram_identity=False)
    b = _summary(500, tier=auth_merge.UserValueTier.TRIAL, has_telegram_identity=True)
    winner, loser = auth_merge._pick_winner(a, b)
    assert winner.user_id == 500
    assert loser.user_id == 900


def test_pick_winner_full_tie_prefers_smaller_user_id():
    a = _summary(700, tier=auth_merge.UserValueTier.TRIAL, has_telegram_identity=False)
    b = _summary(800, tier=auth_merge.UserValueTier.TRIAL, has_telegram_identity=False)
    winner, loser = auth_merge._pick_winner(a, b)
    assert winner.user_id == 700
    assert loser.user_id == 800


def test_build_forfeit_list_includes_trial_lte_and_remnawave():
    loser = _summary(
        100,
        tier=auth_merge.UserValueTier.TRIAL,
        has_trial_flag=True,
        remnawave_uuid="rw-abc",
        lte_gb_total=1,
    )
    forfeit = auth_merge._build_forfeit_list(loser)
    keys = {item.key for item in forfeit}
    assert {"trial_days", "vpn_account", "trial_lte"} == keys


def test_build_forfeit_list_empty_when_no_trial_state():
    loser = _summary(100, tier=auth_merge.UserValueTier.NONE)
    assert auth_merge._build_forfeit_list(loser) == []


@pytest.mark.asyncio
async def test_plan_merge_same_user_returns_no_conflict(monkeypatch):
    user = types.SimpleNamespace(id=10)

    async def _summary_stub(u, *, provider_summaries=None, conn=None):
        return _summary(int(u.id))

    monkeypatch.setattr(auth_merge, "_collect_user_value_summary", _summary_stub)
    monkeypatch.setattr(auth_merge, "_provider_summaries", lambda _uid: _async_empty())

    plan = await auth_merge.plan_merge(user, user, provider="google")
    assert plan.outcome == "no_conflict"
    assert plan.winner is None and plan.loser is None


async def _async_empty():
    return []


@pytest.mark.asyncio
async def test_plan_merge_blocks_when_either_side_is_blocked(monkeypatch):
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)

    async def _summary_stub(u, *, provider_summaries=None, conn=None):
        if int(u.id) == 20:
            return _summary(20, tier=auth_merge.UserValueTier.NONE, is_blocked=True)
        return _summary(10, tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION)

    monkeypatch.setattr(auth_merge, "_collect_user_value_summary", _summary_stub)
    monkeypatch.setattr(auth_merge, "_provider_summaries", lambda _uid: _async_empty())

    plan = await auth_merge.plan_merge(current, other, provider="google")
    assert plan.outcome == "support_required"
    assert plan.reason_code == "blocked_account"


@pytest.mark.asyncio
async def test_plan_merge_refuses_when_both_sides_have_value(monkeypatch):
    """C9: two paying customers cannot be auto-merged."""
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)

    async def _summary_stub(u, *, provider_summaries=None, conn=None):
        return _summary(
            int(u.id),
            tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION,
            has_active_paid_subscription=True,
        )

    monkeypatch.setattr(auth_merge, "_collect_user_value_summary", _summary_stub)
    monkeypatch.setattr(auth_merge, "_provider_summaries", lambda _uid: _async_empty())

    plan = await auth_merge.plan_merge(current, other, provider="google")
    assert plan.outcome == "support_required"
    assert plan.reason_code == "both_have_value"


@pytest.mark.asyncio
async def test_plan_merge_auto_merge_when_loser_is_empty(monkeypatch):
    """C6/C8: the empty side absorbs into the side that has identity/value."""
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)

    async def _summary_stub(u, *, provider_summaries=None, conn=None):
        if int(u.id) == 10:
            return _summary(
                10,
                tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION,
                has_active_paid_subscription=True,
            )
        return _summary(20, tier=auth_merge.UserValueTier.NONE)

    monkeypatch.setattr(auth_merge, "_collect_user_value_summary", _summary_stub)
    monkeypatch.setattr(auth_merge, "_provider_summaries", lambda _uid: _async_empty())

    plan = await auth_merge.plan_merge(current, other, provider="google")
    assert plan.outcome == "auto_merge"
    assert plan.winner.user_id == 10
    assert plan.loser.user_id == 20
    assert plan.forfeit == []


@pytest.mark.asyncio
async def test_plan_merge_requires_confirmation_when_loser_has_trial(monkeypatch):
    """C1: Google trial absorbed by TG with subscription — user must confirm
    the forfeit list before commit."""
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)

    async def _summary_stub(u, *, provider_summaries=None, conn=None):
        if int(u.id) == 10:
            return _summary(
                10,
                tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION,
                has_active_paid_subscription=True,
                has_telegram_identity=True,
            )
        return _summary(
            20,
            tier=auth_merge.UserValueTier.TRIAL,
            has_trial_flag=True,
            remnawave_uuid="rw-trial-1",
        )

    monkeypatch.setattr(auth_merge, "_collect_user_value_summary", _summary_stub)
    monkeypatch.setattr(auth_merge, "_provider_summaries", lambda _uid: _async_empty())

    plan = await auth_merge.plan_merge(current, other, provider="telegram")
    assert plan.outcome == "confirm_required"
    assert plan.reason_code == "trial_forfeit"
    assert plan.winner.user_id == 10
    assert plan.loser.user_id == 20
    forfeit_keys = {item.key for item in plan.forfeit}
    assert "trial_days" in forfeit_keys
    assert "vpn_account" in forfeit_keys


@pytest.mark.asyncio
async def test_plan_merge_admin_always_wins(monkeypatch):
    """C13: admin tier outranks any paid subscription on the other side."""
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)

    async def _summary_stub(u, *, provider_summaries=None, conn=None):
        if int(u.id) == 10:
            return _summary(10, tier=auth_merge.UserValueTier.TRIAL, has_trial_flag=True)
        return _summary(20, tier=auth_merge.UserValueTier.ADMIN, is_admin=True)

    monkeypatch.setattr(auth_merge, "_collect_user_value_summary", _summary_stub)
    monkeypatch.setattr(auth_merge, "_provider_summaries", lambda _uid: _async_empty())

    plan = await auth_merge.plan_merge(current, other, provider="google")
    assert plan.outcome == "confirm_required"
    assert plan.winner.user_id == 20


@pytest.mark.asyncio
async def test_start_link_or_merge_always_issues_token_even_for_empty_loser(monkeypatch):
    """Empty-loser (`auto_merge`) still requires explicit user confirmation.

    Symmetry with the `unlink` biometric gate: every destructive identity
    operation must be user-driven. Returning a preview token instead of
    executing inline closes the OAuth-callback drive-by absorption risk
    that the security review surfaced.
    """
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)

    async def _plan(current_user, other_user, *, provider):
        return auth_merge.MergePlan(
            outcome="auto_merge",
            provider=provider,
            winner=_summary(10, tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION),
            loser=_summary(20, tier=auth_merge.UserValueTier.NONE),
            forfeit=[],
        )

    async def _issue(plan, *, initiated_by_user_id):
        return "preview-tok-empty"

    monkeypatch.setattr(auth_merge, "plan_merge", _plan)
    monkeypatch.setattr(auth_merge, "issue_preview_token", _issue)

    outcome = await web_auth.start_link_or_merge(current, other, provider="google")
    assert outcome["outcome"] == "confirm_required"
    assert outcome["mergeToken"] == "preview-tok-empty"
    assert outcome["plan"]["outcome"] == "auto_merge"


@pytest.mark.asyncio
async def test_start_link_or_merge_issues_token_for_confirm_required(monkeypatch):
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)
    issued: list[tuple[object, int]] = []

    async def _plan(current_user, other_user, *, provider):
        return auth_merge.MergePlan(
            outcome="confirm_required",
            provider=provider,
            winner=_summary(10, tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION),
            loser=_summary(20, tier=auth_merge.UserValueTier.TRIAL, has_trial_flag=True),
            forfeit=[auth_merge.ForfeitItem(key="trial_days", label="trial")],
        )

    async def _issue(plan, *, initiated_by_user_id):
        issued.append((plan, initiated_by_user_id))
        return "preview-tok"

    monkeypatch.setattr(auth_merge, "plan_merge", _plan)
    monkeypatch.setattr(auth_merge, "issue_preview_token", _issue)

    outcome = await web_auth.start_link_or_merge(current, other, provider="google")
    assert outcome["outcome"] == "confirm_required"
    assert outcome["mergeToken"] == "preview-tok"
    assert outcome["expiresIn"] == auth_merge.PREVIEW_TOKEN_TTL_SECONDS
    assert issued and issued[0][1] == 10


@pytest.mark.asyncio
async def test_start_link_or_merge_raises_for_support_required(monkeypatch):
    current = types.SimpleNamespace(id=10)
    other = types.SimpleNamespace(id=20)

    async def _plan(current_user, other_user, *, provider):
        return auth_merge.MergePlan(
            outcome="support_required",
            provider=provider,
            winner=_summary(10),
            loser=_summary(20),
            forfeit=[],
            reason_code="both_have_value",
        )

    monkeypatch.setattr(auth_merge, "plan_merge", _plan)

    with pytest.raises(web_auth.WebAuthError) as exc_info:
        await web_auth.start_link_or_merge(current, other, provider="google")
    assert exc_info.value.code == "merge_requires_support"
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_plan_to_dict_round_trips_forfeit_items():
    plan = auth_merge.MergePlan(
        outcome="confirm_required",
        provider="google",
        winner=_summary(10, tier=auth_merge.UserValueTier.PAID_SUBSCRIPTION, has_telegram_identity=True),
        loser=_summary(20, tier=auth_merge.UserValueTier.TRIAL, has_trial_flag=True),
        forfeit=[
            auth_merge.ForfeitItem(key="trial_days", label="Trial days"),
            auth_merge.ForfeitItem(key="vpn_account", label="VPN account"),
        ],
        reason_code="trial_forfeit",
        reason_text="Trial will be forfeited.",
    )
    payload = plan.to_dict()
    assert payload["outcome"] == "confirm_required"
    assert payload["provider"] == "google"
    assert payload["winner"]["userId"] == 10
    assert payload["loser"]["userId"] == 20
    assert payload["forfeit"][0] == {"key": "trial_days", "label": "Trial days"}

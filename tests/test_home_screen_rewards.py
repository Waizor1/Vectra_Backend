"""Integration tests for home-screen install reward + story-trial grant.

Covers:
  * claim_home_screen_reward idempotency (balance + discount variants)
  * concurrent-claim race (single winner via WHERE timestamp-IS-NULL gate)
  * find_referrer_by_story_code uses the indexed `users.story_code` column
  * Users._grant_story_trial_if_unclaimed: 20 / 1 / 1 bundle, anti-abuse on
    repeat redemption
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
import pytest_asyncio
from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _sqlite_datetime_compat import register_sqlite_datetime_compat

from tests.test_payments_no_yookassa import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _telegram_env():
    os.environ.setdefault("TELEGRAM_TOKEN", "test-bot-token-1234567890ABCDEF")
    os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret")
    os.environ.setdefault("TELEGRAM_WEBAPP_URL", "https://app.example.test/")
    os.environ.setdefault("TELEGRAM_MINIAPP_URL", "https://app.example.test/")
    os.environ.setdefault("ADMIN_TELEGRAM_ID", "1")
    os.environ.setdefault("ADMIN_LOGIN", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin")
    os.environ.setdefault("SCRIPT_DB", "postgres://test")
    os.environ.setdefault("SCRIPT_DEV", "false")
    os.environ.setdefault("SCRIPT_API_URL", "http://test")
    yield


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    register_sqlite_datetime_compat()
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.family_members",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                        "bloobcat.db.referral_rewards",
                        "bloobcat.db.push_subscriptions",
                        "bloobcat.db.home_screen_install_signals",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )

    from bloobcat.db.users import Users

    Users._meta.fk_fields.discard("active_tariff")
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    if users_active_tariff_fk is not None:
        users_active_tariff_fk.reference = False
        users_active_tariff_fk.db_constraint = False

    from tortoise.backends.sqlite.schema_generator import SqliteSchemaGenerator

    client = Tortoise.get_connection("default")
    generator = SqliteSchemaGenerator(client)
    models_to_create = []
    try:
        maybe_models = generator._get_models_to_create(models_to_create)
        if maybe_models is not None:
            models_to_create = maybe_models
    except TypeError:
        models_to_create = generator._get_models_to_create()
    tables = [generator._get_table_sql(model, safe=True) for model in models_to_create]
    creation_sql = "\n".join(
        [t["table_creation_string"] for t in tables]
        + [m for t in tables for m in t["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)

    try:
        yield
    finally:
        await Tortoise.close_connections()


# ── home-screen reward ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_home_screen_balance_reward_credits_50_rub_once():
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import (
        HOME_SCREEN_BALANCE_BONUS_RUB,
        claim_home_screen_reward,
    )

    user = await Users.create(id=100001, full_name="A", balance=10)
    result = await claim_home_screen_reward(user.id, "balance", platform_hint="ios")
    assert result["already_claimed"] is False
    assert result["reward_kind"] == "balance"
    assert result["amount"] == HOME_SCREEN_BALANCE_BONUS_RUB

    refreshed = await Users.get(id=user.id)
    assert refreshed.balance == 10 + HOME_SCREEN_BALANCE_BONUS_RUB
    assert refreshed.home_screen_reward_granted_at is not None
    assert refreshed.home_screen_added_at is not None

    # Second call must be a no-op echoing already_claimed.
    again = await claim_home_screen_reward(user.id, "balance")
    assert again["already_claimed"] is True
    final = await Users.get(id=user.id)
    assert final.balance == 10 + HOME_SCREEN_BALANCE_BONUS_RUB


@pytest.mark.asyncio
async def test_home_screen_discount_reward_creates_personal_discount_once():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import (
        HOME_SCREEN_DISCOUNT_PERCENT,
        HOME_SCREEN_DISCOUNT_TTL_DAYS,
        claim_home_screen_reward,
    )

    user = await Users.create(id=100002, full_name="B", balance=0)
    result = await claim_home_screen_reward(user.id, "discount", platform_hint="android")
    assert result["already_claimed"] is False
    assert result["reward_kind"] == "discount"
    assert result["amount"] == HOME_SCREEN_DISCOUNT_PERCENT

    discounts = await PersonalDiscount.filter(user_id=user.id).all()
    assert len(discounts) == 1
    d = discounts[0]
    assert d.percent == HOME_SCREEN_DISCOUNT_PERCENT
    assert d.remaining_uses == 1
    assert d.source == "home_screen_install"
    # Expires roughly TTL days from now.
    assert d.expires_at == date.today() + timedelta(days=HOME_SCREEN_DISCOUNT_TTL_DAYS)

    # Idempotency: second call must NOT create a second PersonalDiscount row.
    again = await claim_home_screen_reward(user.id, "discount")
    assert again["already_claimed"] is True
    assert await PersonalDiscount.filter(user_id=user.id).count() == 1


@pytest.mark.asyncio
async def test_home_screen_claim_rejects_unknown_reward_kind():
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    await Users.create(id=100003, full_name="C")
    with pytest.raises(ValueError):
        await claim_home_screen_reward(100003, "wat")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_home_screen_claim_raises_for_unknown_user():
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    with pytest.raises(ValueError):
        await claim_home_screen_reward(99999999, "balance")


@pytest.mark.asyncio
async def test_discount_path_rolls_back_flag_when_personaldiscount_create_fails(
    monkeypatch,
):
    """Critical regression: pre-1.79.0 the timestamp UPDATE ran BEFORE
    PersonalDiscount.create(), so a create() failure left the user with
    home_screen_reward_granted_at set but no discount row — a stuck
    'orphan claim'. The 1.79.0 reorder puts create() first inside the
    same transaction so any failure rolls back BOTH writes."""
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users
    from bloobcat.services import home_screen_rewards as svc

    user = await Users.create(id=100010, full_name="rollback-victim", balance=0)

    original_create = PersonalDiscount.create

    async def _failing_create(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("simulated DB failure during PersonalDiscount.create")

    monkeypatch.setattr(PersonalDiscount, "create", _failing_create)

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await svc.claim_home_screen_reward(user.id, "discount", platform_hint="ios")

    monkeypatch.setattr(PersonalDiscount, "create", original_create)

    refreshed = await Users.get(id=user.id)
    assert refreshed.home_screen_reward_granted_at is None, (
        "flag must NOT be set when discount delivery fails"
    )
    assert (
        await PersonalDiscount.filter(user_id=user.id).count() == 0
    ), "no PersonalDiscount row should remain after rollback"

    # User can retry successfully now that the rollback freed the flag.
    result = await svc.claim_home_screen_reward(user.id, "discount", platform_hint="ios")
    assert result["already_claimed"] is False
    assert result["reward_kind"] == "discount"
    final = await Users.get(id=user.id)
    assert final.home_screen_reward_granted_at is not None
    assert await PersonalDiscount.filter(user_id=user.id).count() == 1


@pytest.mark.asyncio
async def test_claim_logs_already_claimed_cache_path(monkeypatch):
    """The pre-1.79.0 service silently returned `{already_claimed: True}`
    on the cache path — production had no log entry to debug missing
    bonuses. 1.79.0 logs every exit."""
    from bloobcat.db.users import Users
    from bloobcat.services import home_screen_rewards as svc

    user = await Users.create(id=100011, full_name="cache-path", balance=0)
    await svc.claim_home_screen_reward(user.id, "balance", platform_hint="android")

    captured: list[str] = []

    def _record(template, *args, **kwargs):  # noqa: ANN001
        try:
            captured.append(template % args)
        except Exception:
            captured.append(str(template))

    monkeypatch.setattr(svc.logger, "info", _record)

    again = await svc.claim_home_screen_reward(
        user.id, "balance", platform_hint="android"
    )
    assert again["already_claimed"] is True

    cache_hits = [m for m in captured if "already-claimed (cache)" in m]
    assert cache_hits, (
        "cache-path return must emit a log line for prod debugging — "
        f"captured: {captured!r}"
    )
    assert str(user.id) in cache_hits[-1]


# ── install-signal ledger (shadow-mode verdict) ──────────────────────


@pytest.mark.asyncio
async def test_signal_ledger_records_strong_verdict_for_appinstalled():
    from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    user = await Users.create(id=110001, full_name="strong", balance=0)
    result = await claim_home_screen_reward(
        user.id, "balance", platform_hint="android", trigger="appinstalled"
    )
    assert result["already_claimed"] is False
    assert result["verdict"] == "strong"

    rows = await HomeScreenInstallSignal.filter(user_id=user.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.trigger == "appinstalled"
    assert row.platform_hint == "android"
    assert row.reward_kind == "balance"
    assert row.verdict == "strong"
    assert row.already_claimed is False
    # No push subscription was inserted in this test.
    assert row.had_active_push_sub is False


@pytest.mark.asyncio
async def test_signal_ledger_records_manual_no_push_verdict():
    """The escape-hatch path (`Я уже добавил иконку`) without an active
    push subscription is the most suspicious bucket — verdict must be
    `manual_no_push` so the funnel dashboard can highlight it. Shadow
    mode: the grant still succeeds."""
    from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    user = await Users.create(id=110002, full_name="manual-no-push", balance=0)
    result = await claim_home_screen_reward(
        user.id, "discount", platform_hint="ios", trigger="manual"
    )
    assert result["already_claimed"] is False
    assert result["verdict"] == "manual_no_push"

    row = await HomeScreenInstallSignal.filter(user_id=user.id).first()
    assert row is not None
    assert row.verdict == "manual_no_push"
    assert row.had_active_push_sub is False
    # Reward still landed — shadow mode does not block.
    refreshed = await Users.get(id=user.id)
    assert refreshed.home_screen_reward_granted_at is not None


@pytest.mark.asyncio
async def test_signal_ledger_promotes_manual_to_with_push_when_subscribed():
    """A user who already has an active web-push subscription is
    proven to have a working standalone client somewhere, so manual
    claims from that account are far less suspicious. Verdict bumps
    to `manual_with_push`."""
    from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
    from bloobcat.db.push_subscriptions import PushSubscription
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    user = await Users.create(id=110003, full_name="manual-with-push", balance=0)
    await PushSubscription.create(
        user_id=user.id,
        endpoint="https://push.example.test/abc",
        p256dh="p256",
        auth="auth",
        is_active=True,
    )

    result = await claim_home_screen_reward(
        user.id, "balance", platform_hint="web", trigger="manual"
    )
    assert result["verdict"] == "manual_with_push"

    row = await HomeScreenInstallSignal.filter(user_id=user.id).first()
    assert row is not None
    assert row.verdict == "manual_with_push"
    assert row.had_active_push_sub is True


@pytest.mark.asyncio
async def test_signal_ledger_unknown_for_legacy_frontend_without_trigger():
    """Older frontends call the endpoint without `trigger`. The ledger
    must accept this and tag the row as `unknown` so we can measure
    rollout adoption."""
    from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    user = await Users.create(id=110004, full_name="legacy", balance=0)
    result = await claim_home_screen_reward(user.id, "balance", platform_hint="tdesktop")
    assert result["verdict"] == "unknown"

    row = await HomeScreenInstallSignal.filter(user_id=user.id).first()
    assert row is not None
    assert row.trigger == "unknown"
    assert row.verdict == "unknown"


@pytest.mark.asyncio
async def test_signal_ledger_normalizes_garbage_trigger_to_unknown():
    """Defensive: if the FE somehow sends an invalid trigger string,
    we coerce it to `unknown` instead of polluting the ledger with
    arbitrary user-controlled values."""
    from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    user = await Users.create(id=110005, full_name="garbage", balance=0)
    result = await claim_home_screen_reward(
        user.id, "balance", trigger="hax0r"  # type: ignore[arg-type]
    )
    assert result["verdict"] == "unknown"

    row = await HomeScreenInstallSignal.filter(user_id=user.id).first()
    assert row is not None
    assert row.trigger == "unknown"


@pytest.mark.asyncio
async def test_signal_ledger_records_duplicate_claim_attempts():
    """Idempotent re-claims must still write a ledger row tagged
    `already_claimed=True` so we can measure how often users tap the
    bonus button multiple times (incl. cross-tab / cross-device)."""
    from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import claim_home_screen_reward

    user = await Users.create(id=110006, full_name="repeat", balance=0)
    await claim_home_screen_reward(
        user.id, "balance", platform_hint="ios", trigger="first_standalone"
    )
    await claim_home_screen_reward(
        user.id, "balance", platform_hint="ios", trigger="boot"
    )

    rows = await HomeScreenInstallSignal.filter(user_id=user.id).order_by("id").all()
    assert len(rows) == 2
    assert rows[0].already_claimed is False
    assert rows[0].verdict == "strong"
    assert rows[1].already_claimed is True
    assert rows[1].verdict == "weak"


@pytest.mark.asyncio
async def test_signal_ledger_rolls_back_with_failed_discount_create(monkeypatch):
    """The ledger row is written inside the same transaction as the
    reward grant, so a discount-create failure must also roll back the
    pending signal row — otherwise the ledger would diverge from the
    actual reward state."""
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.home_screen_install_signals import HomeScreenInstallSignal
    from bloobcat.db.users import Users
    from bloobcat.services import home_screen_rewards as svc

    user = await Users.create(id=110007, full_name="rollback-signal", balance=0)

    async def _failing_create(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("simulated discount-create failure")

    monkeypatch.setattr(PersonalDiscount, "create", _failing_create)
    with pytest.raises(RuntimeError, match="simulated"):
        await svc.claim_home_screen_reward(
            user.id, "discount", platform_hint="ios", trigger="appinstalled"
        )

    assert (
        await HomeScreenInstallSignal.filter(user_id=user.id).count() == 0
    ), "ledger row must roll back together with the failed reward grant"


# ── orphan repair tooling ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_orphans_finds_discount_orphan_only():
    """scan_home_screen_orphans returns users whose flag is set but who
    have no PersonalDiscount(source='home_screen_install') row."""
    from datetime import datetime, timezone

    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import scan_home_screen_orphans

    # Orphan: flag set, no discount.
    orphan = await Users.create(
        id=100020,
        full_name="orphan",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )
    # Consistent: flag set, discount present.
    consistent = await Users.create(
        id=100021,
        full_name="consistent",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )
    await PersonalDiscount.create(
        user_id=consistent.id,
        percent=10,
        is_permanent=False,
        remaining_uses=1,
        expires_at=date.today() + timedelta(days=90),
        source="home_screen_install",
    )
    # Never claimed: flag NULL — must not appear.
    await Users.create(id=100022, full_name="never-claimed", balance=0)

    rows = await scan_home_screen_orphans()
    ids = [r["user_id"] for r in rows]
    assert orphan.id in ids
    assert consistent.id not in ids
    assert 100022 not in ids


@pytest.mark.asyncio
async def test_scan_orphans_respects_user_id_filter():
    from datetime import datetime, timezone

    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import scan_home_screen_orphans

    await Users.create(
        id=100030,
        full_name="other-orphan",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )
    await Users.create(
        id=100031,
        full_name="target-orphan",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )

    rows = await scan_home_screen_orphans(user_id=100031)
    assert len(rows) == 1
    assert rows[0]["user_id"] == 100031


@pytest.mark.asyncio
async def test_scan_orphans_filters_balance_kind_successes_by_default():
    """Balance-kind successful claims look identical to discount-kind
    orphans (granted_at set + no PersonalDiscount). The default scan
    must filter them out by checking balance — otherwise a credit-mode
    repair would double-grant the +50 ₽."""
    from datetime import datetime, timezone

    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import (
        HOME_SCREEN_BALANCE_BONUS_RUB,
        scan_home_screen_orphans,
    )

    # Balance-kind success: flag set, balance == 50, no discount row.
    await Users.create(
        id=100080,
        full_name="balance-success",
        balance=HOME_SCREEN_BALANCE_BONUS_RUB,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )
    # Likely orphan: flag set, balance == 0, no discount row.
    await Users.create(
        id=100081,
        full_name="likely-orphan",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )

    default = await scan_home_screen_orphans()
    default_ids = [r["user_id"] for r in default]
    assert 100081 in default_ids, "likely orphan must be in default scan"
    assert 100080 not in default_ids, "balance-kind success must be filtered"

    with_suspects = await scan_home_screen_orphans(include_balance_suspects=True)
    with_ids = [(r["user_id"], r["likely_orphan"]) for r in with_suspects]
    assert (100081, True) in with_ids
    assert (100080, False) in with_ids


@pytest.mark.asyncio
async def test_repair_clear_mode_drops_flag():
    from datetime import datetime, timezone

    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import repair_home_screen_reward

    user = await Users.create(
        id=100040,
        full_name="clear-target",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )

    result = await repair_home_screen_reward(user.id, "clear", actor="test")
    assert result["repaired"] is True
    assert result["action"] == "cleared_flag"
    refreshed = await Users.get(id=user.id)
    assert refreshed.home_screen_reward_granted_at is None


@pytest.mark.asyncio
async def test_repair_credit_discount_creates_missing_row():
    from datetime import datetime, timezone

    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import (
        HOME_SCREEN_DISCOUNT_PERCENT,
        repair_home_screen_reward,
    )

    user = await Users.create(
        id=100050,
        full_name="discount-orphan",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )

    result = await repair_home_screen_reward(
        user.id, "credit", reward_kind="discount", actor="test"
    )
    assert result["repaired"] is True
    assert result["action"] == "credited_discount"
    discounts = await PersonalDiscount.filter(
        user_id=user.id, source="home_screen_install"
    ).all()
    assert len(discounts) == 1
    assert discounts[0].percent == HOME_SCREEN_DISCOUNT_PERCENT


@pytest.mark.asyncio
async def test_repair_credit_discount_is_noop_when_already_consistent():
    from datetime import datetime, timezone

    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import repair_home_screen_reward

    user = await Users.create(
        id=100051,
        full_name="already-consistent",
        balance=0,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )
    await PersonalDiscount.create(
        user_id=user.id,
        percent=10,
        is_permanent=False,
        remaining_uses=1,
        expires_at=date.today() + timedelta(days=90),
        source="home_screen_install",
    )

    result = await repair_home_screen_reward(
        user.id, "credit", reward_kind="discount", actor="test"
    )
    assert result["repaired"] is False
    assert result["action"] == "already_consistent"
    # No second discount row created.
    assert (
        await PersonalDiscount.filter(
            user_id=user.id, source="home_screen_install"
        ).count()
        == 1
    )


@pytest.mark.asyncio
async def test_repair_credit_balance_force_credits_50_rub():
    """Caller-confirmed force-credit path: adds +50 ₽ even without an
    audit trail. Callers MUST verify the user wasn't already credited."""
    from datetime import datetime, timezone

    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import (
        HOME_SCREEN_BALANCE_BONUS_RUB,
        repair_home_screen_reward,
    )

    user = await Users.create(
        id=100060,
        full_name="balance-force",
        balance=10,
        home_screen_reward_granted_at=datetime.now(timezone.utc),
    )

    result = await repair_home_screen_reward(
        user.id, "credit", reward_kind="balance", actor="admin@example"
    )
    assert result["repaired"] is True
    assert result["action"] == "credited_balance"
    refreshed = await Users.get(id=user.id)
    assert refreshed.balance == 10 + HOME_SCREEN_BALANCE_BONUS_RUB


@pytest.mark.asyncio
async def test_repair_credit_requires_reward_kind():
    from bloobcat.db.users import Users
    from bloobcat.services.home_screen_rewards import repair_home_screen_reward

    await Users.create(id=100070, full_name="kindless")
    with pytest.raises(ValueError, match="credit mode requires reward_kind"):
        await repair_home_screen_reward(100070, "credit")


# ── story-share + referrer lookup ─────────────────────────────────────


@pytest.mark.asyncio
async def test_materialize_story_code_persists_for_o1_lookup():
    from bloobcat.db.users import Users
    from bloobcat.services.story_referral import (
        encode_story_code,
        find_referrer_by_story_code,
        materialize_user_story_code,
    )

    referrer = await Users.create(id=200001, full_name="ref", is_registered=True)
    code = await materialize_user_story_code(referrer.id)
    assert code == encode_story_code(referrer.id)

    refreshed = await Users.get(id=referrer.id)
    assert refreshed.story_code == code

    found = await find_referrer_by_story_code(code)
    assert found == referrer.id


@pytest.mark.asyncio
async def test_find_referrer_returns_none_for_unmaterialized_code():
    from bloobcat.db.users import Users
    from bloobcat.services.story_referral import (
        encode_story_code,
        find_referrer_by_story_code,
    )

    # User exists but never called share-payload, so story_code is NULL.
    user = await Users.create(id=200002, full_name="ref2", is_registered=True)
    code = encode_story_code(user.id)
    assert await find_referrer_by_story_code(code) is None


@pytest.mark.asyncio
async def test_find_referrer_rejects_malformed_code():
    from bloobcat.services.story_referral import find_referrer_by_story_code

    assert await find_referrer_by_story_code("") is None
    assert await find_referrer_by_story_code("NOPE") is None


# ── story-trial grant (20 / 1 / 1) ────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_story_trial_sets_20_days_1_device_1gb():
    from bloobcat.db.users import Users

    user = await Users.create(
        id=300001,
        full_name="story-invitee",
        invite_source="story",
        invited_by_referrer_id=200001,
        referred_by=200001,
        utm="story",
    )
    trial_until = date.today() + timedelta(days=Users.REFERRAL_INVITE_TRIAL_DAYS)
    granted = await Users._grant_referral_trial_if_unclaimed(user.id, trial_until)
    assert granted is True

    refreshed = await Users.get(id=user.id)
    assert refreshed.is_trial is True
    assert refreshed.used_trial is True
    assert refreshed.expired_at == trial_until
    assert refreshed.hwid_limit == Users.REFERRAL_INVITE_HWID_LIMIT == 1
    assert refreshed.lte_gb_total == Users.REFERRAL_INVITE_LTE_GB == 1
    assert refreshed.story_trial_used_at is not None


@pytest.mark.asyncio
async def test_grant_story_trial_skipped_when_referred_by_missing():
    from bloobcat.db.users import Users

    # No referred_by — organic / direct signup must not match the referral-invitee bundle.
    user = await Users.create(id=300002, full_name="no-source")
    granted = await Users._grant_referral_trial_if_unclaimed(
        user.id, date.today() + timedelta(days=20)
    )
    assert granted is False

    refreshed = await Users.get(id=user.id)
    assert refreshed.is_trial is False
    assert refreshed.used_trial is False


@pytest.mark.asyncio
async def test_grant_story_trial_blocked_once_used_at_is_set():
    """Anti-abuse: a user who already redeemed a story-trial cannot get
    another even if their invite_source is still tagged."""
    from datetime import datetime, timezone

    from bloobcat.db.users import Users

    user = await Users.create(
        id=300003,
        full_name="repeat-attempt",
        invite_source="story",
        referred_by=200001,
        utm="story",
        story_trial_used_at=datetime.now(timezone.utc),
    )
    granted = await Users._grant_referral_trial_if_unclaimed(
        user.id, date.today() + timedelta(days=20)
    )
    assert granted is False


# ── resolve_referral_from_start_param: story_<code> branch ──────────


@pytest.mark.asyncio
async def test_resolve_referral_routes_story_param_to_story_utm_marker():
    from bloobcat.db.users import Users
    from bloobcat.funcs.referral_attribution import resolve_referral_from_start_param
    from bloobcat.services.story_referral import materialize_user_story_code

    referrer = await Users.create(id=400001, full_name="referrer", is_registered=True)
    code = await materialize_user_story_code(referrer.id)

    referred_by, utm = await resolve_referral_from_start_param(f"story_{code}")
    assert referred_by == referrer.id
    assert utm == "story"


@pytest.mark.asyncio
async def test_resolve_referral_returns_story_marker_for_well_formed_unknown_code():
    """A structurally-well-formed code whose referrer we cannot resolve
    (e.g. user is not yet in the cache) still tags invite_source='story'
    so the trial-grant branch fires. Real referrer attribution is a
    "nice to have" for analytics, not a gate on the bonus."""
    from bloobcat.funcs.referral_attribution import resolve_referral_from_start_param
    from bloobcat.services.story_referral import encoded_story_code_length

    # Construct a structurally valid but unknown code: 'STORY' + uppercase
    # base32 of the right length, but for a user that never materialized.
    valid_unknown_code = "STORY" + "A" * (encoded_story_code_length() - len("STORY"))
    referred_by, utm = await resolve_referral_from_start_param(f"story_{valid_unknown_code}")
    assert referred_by == 0
    assert utm == "story"


@pytest.mark.asyncio
async def test_resolve_referral_rejects_malformed_story_code():
    """P1 guard (daily bug scan 2026-05-12): an attacker fabricating
    `startapp=story_BADCODE` MUST NOT receive the 20-day story trial.
    Malformed codes return (0, None) so the registration flow falls
    through to the regular 10-day trial path."""
    from bloobcat.funcs.referral_attribution import resolve_referral_from_start_param

    for malformed in (
        "story_",                                  # empty payload
        "story_TOOSHORT",                          # below minimum length
        "story_STORYWITHTRAILINGJUNK_TOOLONG",     # wrong length
        "story_storylowercaseinstead",             # lowercase fails base32 regex
        "story_STORY1!@#$%^&*",                    # non-base32 chars
        "story_NOTSTORYPREFIX12",                  # missing STORY prefix
    ):
        referred_by, utm = await resolve_referral_from_start_param(malformed)
        assert referred_by == 0, f"{malformed!r} returned non-zero referrer"
        assert utm is None, f"{malformed!r} returned story marker — would grant 20d trial"

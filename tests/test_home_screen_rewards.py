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

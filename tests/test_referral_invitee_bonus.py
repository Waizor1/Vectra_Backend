"""Regression tests for the referral-invitee trial bundle (spec 2026-05-12).

Pins the contract that:
  * Any user with a non-partner `referred_by` gets the 20d / 1 GB LTE / hwid=1 bundle.
  * Partner / QR referrals are excluded — they have their own economy.
  * The grant is idempotent: a second call returns False.
  * Story referrals (legacy path) continue to receive the bundle (regression guard).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

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
def _install_stubs_once(_telegram_env):
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


async def _create_referrer(user_id: int = 1) -> "object":
    from bloobcat.db.users import Users

    today = date.today()
    return await Users.create(
        id=user_id,
        full_name="referrer",
        is_registered=True,
        expired_at=today + timedelta(days=30),
    )


@pytest.mark.asyncio
async def test_ref_link_invitee_gets_20day_trial_and_1gb_lte():
    """A user who arrived via `?start=ref_<id>` (utm is NULL) gets the
    invitee bundle: 20 days, 1 GB LTE, hwid_limit = 1."""
    from bloobcat.db.users import Users

    await _create_referrer(user_id=10001)
    invitee = await Users.create(
        id=10002,
        full_name="ref-invitee",
        is_registered=True,
        referred_by=10001,
        utm=None,
    )
    trial_until = date.today() + timedelta(days=Users.REFERRAL_INVITE_TRIAL_DAYS)

    granted = await Users._grant_referral_trial_if_unclaimed(invitee.id, trial_until)
    assert granted is True

    refreshed = await Users.get(id=invitee.id)
    assert refreshed.is_trial is True
    assert refreshed.used_trial is True
    assert refreshed.expired_at == trial_until
    assert refreshed.hwid_limit == 1
    assert refreshed.lte_gb_total == 1
    assert refreshed.story_trial_used_at is not None


@pytest.mark.asyncio
async def test_partner_referral_invitee_does_not_get_invitee_bonus():
    """A partner-attributed signup (utm='partner') must NOT receive the
    invitee bundle — partner economics live in PartnerEarnings."""
    from bloobcat.db.users import Users

    await _create_referrer(user_id=10101)
    partner_invitee = await Users.create(
        id=10102,
        full_name="partner-invitee",
        is_registered=True,
        referred_by=10101,
        utm="partner",
    )

    granted = await Users._grant_referral_trial_if_unclaimed(
        partner_invitee.id, date.today() + timedelta(days=20)
    )
    assert granted is False

    refreshed = await Users.get(id=partner_invitee.id)
    assert refreshed.is_trial is False
    assert refreshed.used_trial is False
    assert refreshed.story_trial_used_at is None


@pytest.mark.asyncio
async def test_qr_referral_invitee_does_not_get_invitee_bonus():
    """A QR-attributed signup (utm starts with 'qr_') must NOT receive the
    invitee bundle — same exclusion as partner."""
    from bloobcat.db.users import Users

    await _create_referrer(user_id=10201)
    qr_invitee = await Users.create(
        id=10202,
        full_name="qr-invitee",
        is_registered=True,
        referred_by=10201,
        utm="qr_demo_token",
    )

    granted = await Users._grant_referral_trial_if_unclaimed(
        qr_invitee.id, date.today() + timedelta(days=20)
    )
    assert granted is False

    refreshed = await Users.get(id=qr_invitee.id)
    assert refreshed.is_trial is False
    assert refreshed.used_trial is False
    assert refreshed.story_trial_used_at is None


@pytest.mark.asyncio
async def test_invitee_bonus_idempotent():
    """A second grant call for the same user returns False (anti-abuse
    via story_trial_used_at sentinel)."""
    from bloobcat.db.users import Users

    await _create_referrer(user_id=10301)
    invitee = await Users.create(
        id=10302,
        full_name="idempotent-invitee",
        is_registered=True,
        referred_by=10301,
        utm=None,
    )
    trial_until = date.today() + timedelta(days=Users.REFERRAL_INVITE_TRIAL_DAYS)

    first = await Users._grant_referral_trial_if_unclaimed(invitee.id, trial_until)
    second = await Users._grant_referral_trial_if_unclaimed(invitee.id, trial_until)

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_story_referral_invitee_still_works():
    """Regression guard: story-share signups continue to receive the bundle
    exactly as before — they are non-partner referrals just like ref_*."""
    from bloobcat.db.users import Users

    await _create_referrer(user_id=10401)
    story_invitee = await Users.create(
        id=10402,
        full_name="story-invitee",
        is_registered=True,
        referred_by=10401,
        invite_source="story",
        invited_by_referrer_id=10401,
        utm="story",
    )
    trial_until = date.today() + timedelta(days=Users.REFERRAL_INVITE_TRIAL_DAYS)

    granted = await Users._grant_referral_trial_if_unclaimed(story_invitee.id, trial_until)
    assert granted is True

    refreshed = await Users.get(id=story_invitee.id)
    assert refreshed.is_trial is True
    assert refreshed.used_trial is True
    assert refreshed.expired_at == trial_until
    assert refreshed.hwid_limit == 1
    assert refreshed.lte_gb_total == 1
    assert refreshed.story_trial_used_at is not None


@pytest.mark.asyncio
async def test_legacy_alias_grant_story_trial_still_works():
    """Backward-compat alias: external callers importing
    `_grant_story_trial_if_unclaimed` must keep working."""
    from bloobcat.db.users import Users

    await _create_referrer(user_id=10501)
    invitee = await Users.create(
        id=10502,
        full_name="legacy-alias-invitee",
        is_registered=True,
        referred_by=10501,
        utm=None,
    )
    trial_until = date.today() + timedelta(days=Users.STORY_TRIAL_DAYS)

    granted = await Users._grant_story_trial_if_unclaimed(invitee.id, trial_until)
    assert granted is True
    assert Users.STORY_TRIAL_DAYS == 20
    assert Users.STORY_TRIAL_HWID_LIMIT == 1
    assert Users.STORY_TRIAL_LTE_GB == 1


@pytest.mark.asyncio
async def test_used_trial_blocks_invitee_bundle_for_legacy_user():
    """Legacy user who already consumed regular trial cannot reclaim the rich
    invitee bundle even if they later somehow have referred_by populated.

    The `used_trial=False` filter in `_grant_referral_trial_if_unclaimed` is
    the safety latch here.
    """
    from bloobcat.db.users import Users

    await _create_referrer(user_id=10601)
    invitee = await Users.create(
        id=10602,
        full_name="legacy-used-trial-invitee",
        is_registered=True,
        referred_by=10601,
        used_trial=True,  # legacy: already consumed regular trial
        story_trial_used_at=None,  # never claimed referral bundle
    )

    trial_until = date.today() + timedelta(days=20)
    granted = await Users._grant_referral_trial_if_unclaimed(invitee.id, trial_until)
    assert granted is False

    refreshed = await Users.get(id=invitee.id)
    assert refreshed.story_trial_used_at is None  # still unclaimed
    assert refreshed.lte_gb_total != 1  # bundle NOT applied

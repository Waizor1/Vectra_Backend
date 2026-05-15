from __future__ import annotations

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


@pytest.mark.parametrize(
    ("paid_count", "key", "percent"),
    [
        (0, "bronze", 20),
        (1, "silver", 25),
        (2, "silver", 25),
        (3, "gold", 30),
        (5, "gold", 30),
        (6, "platinum", 35),
        (8, "platinum", 35),
        (9, "diamond", 40),
        (15, "diamond", 40),
        (50, "diamond", 40),
    ],
)
def test_referral_level_thresholds(paid_count, key, percent):
    from bloobcat.services.referral_gamification import get_referral_level

    level = get_referral_level(paid_count)

    assert level.key == key
    assert level.cashback_percent == percent


def test_referral_levels_baseline_is_bronze_with_twenty_percent():
    """Spec 2026-05-15: linear scale 20/25/30/35/40. Bronze baseline
    starts at 20% cashback at zero paid friends. Next level is silver (25%)."""
    from bloobcat.services.referral_gamification import (
        REFERRAL_LEVELS,
        get_next_referral_level,
        get_referral_level,
    )

    assert len(REFERRAL_LEVELS) == 5
    assert REFERRAL_LEVELS[0].key == "bronze"
    assert get_referral_level(0).key == "bronze"
    assert get_referral_level(0).cashback_percent == 20

    next_level = get_next_referral_level(0)
    assert next_level is not None
    assert next_level.key == "silver"
    assert next_level.cashback_percent == 25


async def _make_first_payment_pair(*, referrer_id: int = 2001, referred_id: int = 2002, utm=None, partner=False):
    from bloobcat.db.referral_rewards import ReferralRewards
    from bloobcat.db.users import Users

    today = date.today()
    referrer = await Users.create(
        id=referrer_id,
        username=f"ref-{referrer_id}",
        full_name="Referral Owner",
        is_registered=True,
        is_partner=partner,
        balance=0,
        expired_at=today + timedelta(days=30),
    )
    referred = await Users.create(
        id=referred_id,
        username=f"friend-{referred_id}",
        full_name="Paid Friend",
        is_registered=True,
        referred_by=referrer.id,
        utm=utm,
        expired_at=today + timedelta(days=5),
    )
    if not partner and not (isinstance(utm, str) and (utm == "partner" or utm.startswith("qr_"))):
        await ReferralRewards.create(
            referred_user_id=int(referred.id),
            referrer_user_id=int(referrer.id),
            kind="first_payment",
            payment_id=f"first-{referred.id}",
            friend_bonus_days=7,
            referrer_bonus_days=0,
        )
    return referrer, referred


@pytest.mark.asyncio
async def test_awards_standard_cashback_from_external_payment_and_creates_chest():
    from bloobcat.db.referral_rewards import ReferralCashbackRewards, ReferralLevelRewards
    from bloobcat.db.users import Users
    from bloobcat.services.referral_gamification import award_referral_cashback

    referrer, referred = await _make_first_payment_pair()

    res = await award_referral_cashback(
        payment_id="ordinary-ext-1",
        referral_user=referred,
        amount_external_rub=1000,
    )

    # With the 2026-05-15 linear scale (20/25/30/35/40), 1 paid friend lands
    # the referrer on silver (25%) — the first referral itself is the entry to
    # the silver tier, and silver chest is auto-created alongside bronze.
    assert res["applied"] is True
    assert res["cashback_percent"] == 25
    assert res["level_key"] == "silver"
    assert res["reward_rub"] == 250

    referrer_after = await Users.get(id=referrer.id)
    assert int(referrer_after.balance or 0) == 250

    ledger = await ReferralCashbackRewards.get(payment_id="ordinary-ext-1")
    assert int(ledger.reward_rub or 0) == 250
    assert int(ledger.amount_external_rub or 0) == 1000

    silver_chest = await ReferralLevelRewards.get(user_id=referrer.id, level_key="silver")
    assert silver_chest.status == "available"
    bronze_chest = await ReferralLevelRewards.get(user_id=referrer.id, level_key="bronze")
    assert bronze_chest.status == "available"


@pytest.mark.asyncio
async def test_skips_cashback_for_pure_balance_payment():
    from bloobcat.db.referral_rewards import ReferralCashbackRewards
    from bloobcat.db.users import Users
    from bloobcat.services.referral_gamification import award_referral_cashback

    referrer, referred = await _make_first_payment_pair(referrer_id=2101, referred_id=2102)

    res = await award_referral_cashback(
        payment_id="ordinary-balance-only",
        referral_user=referred,
        amount_external_rub=0,
    )

    assert res["applied"] is False
    assert res["reason"] == "no_external_amount"
    assert await ReferralCashbackRewards.all().count() == 0
    assert int((await Users.get(id=referrer.id)).balance or 0) == 0


@pytest.mark.asyncio
async def test_cashback_is_idempotent_by_payment_id():
    from bloobcat.db.referral_rewards import ReferralCashbackRewards
    from bloobcat.db.users import Users
    from bloobcat.services.referral_gamification import award_referral_cashback

    referrer, referred = await _make_first_payment_pair(referrer_id=2201, referred_id=2202)

    first = await award_referral_cashback(
        payment_id="ordinary-idempotent",
        referral_user=referred,
        amount_external_rub=1000,
    )
    second = await award_referral_cashback(
        payment_id="ordinary-idempotent",
        referral_user=referred,
        amount_external_rub=1000,
    )

    assert first["applied"] is True
    assert second["applied"] is False
    assert second["reason"] == "duplicate_payment"
    assert await ReferralCashbackRewards.all().count() == 1
    # 1 paid friend → silver (25%) under the 2026-05-15 linear scale.
    assert int((await Users.get(id=referrer.id)).balance or 0) == 250


@pytest.mark.asyncio
async def test_partner_and_qr_attribution_skip_standard_cashback():
    from bloobcat.db.referral_rewards import ReferralCashbackRewards
    from bloobcat.db.users import Users
    from bloobcat.services.referral_gamification import award_referral_cashback

    partner, partner_ref = await _make_first_payment_pair(
        referrer_id=2301, referred_id=2302, partner=True
    )
    qr_referrer, qr_ref = await _make_first_payment_pair(
        referrer_id=2311, referred_id=2312, utm="qr_demo"
    )

    partner_res = await award_referral_cashback(
        payment_id="partner-skip",
        referral_user=partner_ref,
        amount_external_rub=1000,
    )
    qr_res = await award_referral_cashback(
        payment_id="qr-skip",
        referral_user=qr_ref,
        amount_external_rub=1000,
    )

    assert partner_res["applied"] is False
    assert partner_res["reason"] == "partner_referrer"
    assert qr_res["applied"] is False
    assert qr_res["reason"] == "partner_source"
    assert await ReferralCashbackRewards.all().count() == 0
    assert int((await Users.get(id=partner.id)).balance or 0) == 0
    assert int((await Users.get(id=qr_referrer.id)).balance or 0) == 0


@pytest.mark.asyncio
async def test_chest_created_once_and_opened_once(monkeypatch):
    from bloobcat.db.referral_rewards import ReferralLevelRewards
    from bloobcat.db.users import Users
    from bloobcat.services import referral_gamification
    from bloobcat.services.referral_gamification import (
        ensure_referral_level_rewards,
        open_referral_chest,
    )

    user = await Users.create(
        id=2401,
        username="chest-owner",
        full_name="Chest Owner",
        balance=0,
    )

    created = await ensure_referral_level_rewards(user_id=int(user.id), paid_friends_count=3)
    created_again = await ensure_referral_level_rewards(user_id=int(user.id), paid_friends_count=3)

    # 3 paid friends reaches gold under the 2026-05-15 linear scale; bronze, silver,
    # and gold chests are auto-created on first pass and idempotent on the second.
    assert {row.level_key for row in created} == {"bronze", "silver", "gold"}
    assert created_again == []
    assert await ReferralLevelRewards.filter(user_id=user.id).count() == 3

    bronze = await ReferralLevelRewards.get(user_id=user.id, level_key="bronze")
    monkeypatch.setattr(referral_gamification.random, "random", lambda: 0.1)
    reward = await open_referral_chest(user=user, chest_id=int(bronze.id))
    second = await open_referral_chest(user=user, chest_id=int(bronze.id))

    assert reward is not None
    assert reward["type"] == "balance"
    assert reward["value"] == 50
    assert second is None
    assert int((await Users.get(id=user.id)).balance or 0) == 50
    assert (await ReferralLevelRewards.get(id=bronze.id)).status == "opened"


@pytest.mark.asyncio
async def test_backfill_style_chests_do_not_create_retro_cashback():
    from bloobcat.db.referral_rewards import ReferralCashbackRewards, ReferralLevelRewards, ReferralRewards
    from bloobcat.db.users import Users
    from bloobcat.services.referral_gamification import ensure_referral_level_rewards

    referrer = await Users.create(id=2501, username="backfill", full_name="Backfill Owner")
    for idx in range(3):
        friend = await Users.create(
            id=2502 + idx,
            username=f"paid-{idx}",
            full_name="Paid Friend",
            referred_by=referrer.id,
        )
        await ReferralRewards.create(
            referred_user_id=int(friend.id),
            referrer_user_id=int(referrer.id),
            kind="first_payment",
            payment_id=f"old-{idx}",
            friend_bonus_days=7,
            referrer_bonus_days=0,
        )

    paid_count = await ReferralRewards.filter(referrer_user_id=referrer.id, kind="first_payment").count()
    await ensure_referral_level_rewards(user_id=int(referrer.id), paid_friends_count=paid_count)

    assert await ReferralCashbackRewards.all().count() == 0
    # 3 paid friends → bronze + silver + gold chests under 2026-05-15 linear scale.
    assert await ReferralLevelRewards.filter(user_id=referrer.id).count() == 3

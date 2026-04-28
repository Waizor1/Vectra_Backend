from __future__ import annotations

from datetime import date, timedelta
import warnings

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
                        "bloobcat.db.partner_qr",
                        "bloobcat.db.partner_earnings",
                        "bloobcat.db.partner_withdrawals",
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


@pytest.mark.asyncio
async def test_partner_source_first_payment_skips_referral_rewards_but_cashback_still_works():
    from bloobcat.db.partner_earnings import PartnerEarnings
    from bloobcat.db.referral_rewards import ReferralRewards
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import (
        _apply_referral_first_payment_reward,
        _award_partner_cashback,
    )

    today = date.today()
    partner = await Users.create(
        id=8801,
        username="partner",
        full_name="Partner User",
        is_registered=True,
        is_partner=True,
        custom_referral_percent=10,
        balance=0,
        expired_at=today + timedelta(days=30),
    )
    referred = await Users.create(
        id=8802,
        username="referred",
        full_name="Referred User",
        is_registered=True,
        referred_by=partner.id,
        utm="partner",
        expired_at=today + timedelta(days=9),
    )

    reward_res = await _apply_referral_first_payment_reward(
        referred_user_id=int(referred.id),
        payment_id="partner-source-no-ref-days",
        amount_rub=1000,
        months=1,
        device_count=1,
    )

    assert reward_res["applied"] is False
    assert await ReferralRewards.all().count() == 0

    partner_after = await Users.get(id=partner.id)
    assert int(partner_after.referral_bonus_days_total or 0) == 0
    assert int(partner_after.balance or 0) == 0

    referred_after = await Users.get(id=referred.id)
    assert referred_after.referral_first_payment_rewarded is False
    assert referred_after.expired_at == referred.expired_at

    await _award_partner_cashback(
        payment_id="partner-source-cashback",
        referral_user=referred_after,
        amount_rub_total=1000,
    )

    partner_after = await Users.get(id=partner.id)
    assert int(partner_after.balance or 0) == 100

    earning = await PartnerEarnings.get(payment_id="partner-source-cashback")
    assert int(earning.reward_rub or 0) == 100
    assert earning.source == "referral_link"


@pytest.mark.asyncio
async def test_referrals_status_excludes_partner_and_qr_sources():
    from bloobcat.db.users import Users
    from bloobcat.funcs.referral_attribution import build_referral_link
    from bloobcat.routes import referrals as referrals_module

    owner = await Users.create(
        id=8901,
        username="owner",
        full_name="Referral Owner",
        referral_bonus_days_total=21,
    )
    await Users.create(
        id=8902,
        username="plain-ref",
        full_name="Plain Referral",
        referred_by=owner.id,
        utm=None,
    )
    await Users.create(
        id=8903,
        username="campaign-ref",
        full_name="Campaign Referral",
        referred_by=owner.id,
        utm="campaign_x",
    )
    await Users.create(
        id=8904,
        username="partner-ref",
        full_name="Partner Referral",
        referred_by=owner.id,
        utm="partner",
    )
    await Users.create(
        id=8905,
        username="qr-ref",
        full_name="Qr Referral",
        referred_by=owner.id,
        utm="qr_demo_token",
    )

    status = await referrals_module.get_status(user=owner)

    assert status.friendsCount == 2
    assert status.totalBonusDays == 21
    assert status.referralLink == await build_referral_link(int(owner.id))


@pytest.mark.asyncio
async def test_partner_routes_return_partner_link_and_keep_all_invites():
    from bloobcat.db.users import Users
    from bloobcat.funcs.referral_attribution import build_partner_link
    from bloobcat.routes import partner as partner_module

    partner = await Users.create(
        id=9001,
        username="partner-owner",
        full_name="Partner Owner",
        is_partner=True,
        custom_referral_percent=17,
        balance=350,
    )
    await Users.create(
        id=9002,
        username="plain",
        full_name="Plain Invite",
        referred_by=partner.id,
        utm=None,
    )
    await Users.create(
        id=9003,
        username="partner-marker",
        full_name="Partner Marker Invite",
        referred_by=partner.id,
        utm="partner",
    )
    await Users.create(
        id=9004,
        username="qr-marker",
        full_name="Qr Marker Invite",
        referred_by=partner.id,
        utm="qr_partner_demo",
    )

    status = await partner_module.get_status(user=partner)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        summary = await partner_module.get_summary(user=partner)
    expected_link = await build_partner_link(int(partner.id))

    assert status.isPartner is True
    assert status.cashbackPercent == 17
    assert status.referralLink == expected_link

    assert summary.isPartner is True
    assert summary.cashbackPercent == 17
    assert summary.invitedCount == 3
    assert summary.referralLink == expected_link

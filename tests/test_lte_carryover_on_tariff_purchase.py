"""Регресс-тест: при покупке нового тарифа неиспользованные LTE-гигабайты
из предыдущего ActiveTariff должны переноситься на новый период.

До фикса 2026-05-14 (1.71.0): при смене активного тарифа код считал денежный
рефанд `remaining_gb * old_active_tariff.lte_price_per_gb` и начислял его
на баланс. Если old.lte_price_per_gb == 0 (typical для триальных /
synthetic-promo тарифов после LTE top-up — top-up прибавляет GB, но не
обновляет lte_price_per_gb), рефанд получался нулевой, GB новые не
создавались, и юзер терял заплаченные гигабайты.

Сценарий пользователя (rutracker bonus trial + LTE top-up):
1. Пробный/промо тариф с lte_gb_total=1, lte_price_per_gb=0 (триал бесплатный).
2. LTE top-up на 10 GB → active_tariff.lte_gb_total = 11, lte_price_per_gb
   остаётся 0 (топап не обновляет цену в этом снапшоте).
3. Покупка обычного тарифа без LTE → lte_gb=0 в metadata.
4. До фикса: user.lte_gb_total = 0 (потеря 11 GB).
5. После фикса: user.lte_gb_total = 11 (carryover GB на новый период).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
    from tests.test_payments_no_yookassa import install_stubs
except ModuleNotFoundError:  # pragma: no cover - import path compat
    from _sqlite_datetime_compat import register_sqlite_datetime_compat
    from test_payments_no_yookassa import install_stubs


register_sqlite_datetime_compat()


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.tariff",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.family_members",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                        "bloobcat.db.notifications",
                        "bloobcat.db.referral_rewards",
                        "bloobcat.db.subscription_freezes",
                        "bloobcat.db.segment_campaigns",
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


def test_compute_lte_carryover_gb_unit():
    """Чистый unit-тест: формула carryover-а возвращает remaining GB, округляя
    дробное использование в пользу юзера (floor по lte_gb_used)."""
    from types import SimpleNamespace

    from bloobcat.routes.payment import _compute_lte_carryover_gb

    assert _compute_lte_carryover_gb(None) == 0
    assert (
        _compute_lte_carryover_gb(SimpleNamespace(lte_gb_total=11, lte_gb_used=0.0))
        == 11
    )
    # used=2.3 → floor=2 → carryover=9 (не 8, юзеру в плюс).
    assert (
        _compute_lte_carryover_gb(SimpleNamespace(lte_gb_total=11, lte_gb_used=2.3))
        == 9
    )
    # Полностью использован — carryover 0.
    assert (
        _compute_lte_carryover_gb(SimpleNamespace(lte_gb_total=5, lte_gb_used=5.0))
        == 0
    )
    # Перерасход (lte_gb_used > total) не уходит в минус.
    assert (
        _compute_lte_carryover_gb(SimpleNamespace(lte_gb_total=5, lte_gb_used=7.0))
        == 0
    )
    # Граничные None/0 значения.
    assert (
        _compute_lte_carryover_gb(SimpleNamespace(lte_gb_total=None, lte_gb_used=None))
        == 0
    )


@pytest.mark.asyncio
async def test_pay_from_balance_carries_over_unused_lte_to_new_tariff():
    """Покупка тарифа без LTE с активным предыдущим тарифом, где есть
    неиспользованные GB (топап на триале) → carryover в новый ActiveTariff."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import pay

    user = await Users.create(
        id=9101,
        username="carryover-trial",
        full_name="Carryover Trial User",
        balance=10_000,
        is_registered=True,
    )

    # Промо/триал ActiveTariff с lte_price_per_gb=0 (триал бесплатный),
    # но lte_gb_total=11 после top-up'a 10 GB поверх 1 GB бонуса.
    old_active = await ActiveTariffs.create(
        id=9101,
        user_id=user.id,
        name="Промо-активация",
        months=1,
        price=0,
        hwid_limit=1,
        lte_gb_total=11,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = old_active.id
    user.lte_gb_total = 11
    await user.save(update_fields=["active_tariff_id", "lte_gb_total"])

    # Новый тариф БЕЗ LTE (lte_gb_total на самом тарифе = 0).
    new_tariff = await Tariffs.create(
        id=9102,
        name="Без LTE",
        months=1,
        base_price=500,
        progressive_multiplier=0.9,
        order=1,
        is_active=True,
    )
    new_tariff.lte_gb_total = 0
    new_tariff.lte_price_per_gb = 0.0
    await new_tariff.save()

    result = await pay(
        tariff_id=new_tariff.id,
        email="carryover@example.com",
        device_count=1,
        user=user,
    )

    assert result["status"] == "success", result

    refreshed = await Users.get(id=user.id)
    assert refreshed.active_tariff_id is not None
    assert refreshed.active_tariff_id != old_active.id, (
        "old ActiveTariff должен быть заменён на новый"
    )

    new_active = await ActiveTariffs.get(id=refreshed.active_tariff_id)
    # Главный инвариант: 11 GB перенеслись на новый тариф, несмотря на то,
    # что сам тариф «без LTE» (lte_gb=0). После фикса new_active.lte_gb_total
    # = lte_gb_purchased(0) + carryover(11) = 11.
    assert new_active.lte_gb_total == 11, (
        f"ожидался carryover 11 GB на новый ActiveTariff, "
        f"получили {new_active.lte_gb_total}"
    )
    assert refreshed.lte_gb_total == 11, (
        f"user.lte_gb_total должен синкаться с новым ActiveTariff, "
        f"получили {refreshed.lte_gb_total}"
    )
    # ActiveTariff действительно сменился.
    deleted = await ActiveTariffs.get_or_none(id=old_active.id)
    assert deleted is None, "old ActiveTariff должен быть удалён"


@pytest.mark.asyncio
async def test_pay_from_balance_carries_over_with_partial_lte_used():
    """Если часть LTE использована (lte_gb_used > 0), переносится остаток
    с округлением floor по used в пользу юзера."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import pay

    user = await Users.create(
        id=9201,
        username="carryover-partial",
        full_name="Carryover Partial User",
        balance=10_000,
        is_registered=True,
    )
    old_active = await ActiveTariffs.create(
        id=9201,
        user_id=user.id,
        name="Промо-активация",
        months=1,
        price=0,
        hwid_limit=1,
        lte_gb_total=11,
        lte_gb_used=2.3,  # floor(2.3)=2 → carryover=9
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = old_active.id
    user.lte_gb_total = 11
    await user.save(update_fields=["active_tariff_id", "lte_gb_total"])

    new_tariff = await Tariffs.create(
        id=9202,
        name="Без LTE 2",
        months=1,
        base_price=500,
        progressive_multiplier=0.9,
        order=1,
        is_active=True,
    )
    new_tariff.lte_gb_total = 0
    new_tariff.lte_price_per_gb = 0.0
    await new_tariff.save()

    result = await pay(
        tariff_id=new_tariff.id,
        email="carryover-partial@example.com",
        device_count=1,
        user=user,
    )
    assert result["status"] == "success", result

    refreshed = await Users.get(id=user.id)
    new_active = await ActiveTariffs.get(id=refreshed.active_tariff_id)
    assert new_active.lte_gb_total == 9, (
        f"ожидался carryover 9 GB (11 total - floor(2.3 used) = 9), "
        f"получили {new_active.lte_gb_total}"
    )
    assert refreshed.lte_gb_total == 9

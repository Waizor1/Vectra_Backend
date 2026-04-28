import types
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _sqlite_datetime_compat import register_sqlite_datetime_compat

try:
    from tests._payment_test_stubs import install_stubs
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _payment_test_stubs import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    restore_stubs = install_stubs()
    try:
        yield
    finally:
        restore_stubs()


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
                        "bloobcat.db.tariff",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.family_members",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                        "bloobcat.db.notifications",
                        "bloobcat.db.referral_rewards",
                        "bloobcat.db.subscription_freezes",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )

    from bloobcat.db.users import Users

    had_active_tariff_fk = "active_tariff" in Users._meta.fk_fields
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    original_active_tariff_reference = None
    original_active_tariff_db_constraint = None

    Users._meta.fk_fields.discard("active_tariff")
    if users_active_tariff_fk is not None:
        original_active_tariff_reference = users_active_tariff_fk.reference
        original_active_tariff_db_constraint = users_active_tariff_fk.db_constraint
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
        if had_active_tariff_fk:
            Users._meta.fk_fields.add("active_tariff")
        if users_active_tariff_fk is not None:
            if original_active_tariff_reference is not None:
                users_active_tariff_fk.reference = original_active_tariff_reference
            if original_active_tariff_db_constraint is not None:
                users_active_tariff_fk.db_constraint = (
                    original_active_tariff_db_constraint
                )
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_family_purchase_freezes_base_and_resume_restores_it(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback
    from bloobcat.services.subscription_overlay import resume_frozen_base_if_due

    consumed_discount_ids: list[int | None] = []

    async def _consume_discount(discount_id):
        consumed_discount_ids.append(discount_id)
        return True

    monkeypatch.setattr(payment_module, "consume_discount_if_needed", _consume_discount)

    today = date.today()
    user = await Users.create(
        id=9101,
        username="u9101",
        full_name="User 9101",
        is_registered=True,
        expired_at=today + timedelta(days=20),
        hwid_limit=3,
    )
    base_active = await ActiveTariffs.create(
        user=user,
        name="base",
        months=1,
        price=1000,
        hwid_limit=3,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = base_active.id
    await user.save(update_fields=["active_tariff_id"])

    family_tariff = await Tariffs.create(
        id=9102,
        name="family_12m",
        months=12,
        base_price=4490,
        progressive_multiplier=0.9,
        order=1,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    yk_payment = types.SimpleNamespace(
        id="family-9101",
        amount=types.SimpleNamespace(value="4490.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 12,
        "tariff_id": family_tariff.id,
        "device_count": 10,
        "amount_from_balance": 0,
        "lte_gb": 0,
        "discount_id": 501,
    }

    applied = await _apply_succeeded_payment_fallback(yk_payment, user, metadata)
    assert applied is True
    assert consumed_discount_ids == [501]

    freeze = await SubscriptionFreezes.get(user_id=user.id, is_active=True)
    assert freeze.base_remaining_days >= 19
    base_remaining_days = int(freeze.base_remaining_days)

    await SubscriptionFreezes.filter(id=freeze.id).update(
        family_expires_at=today - timedelta(days=1)
    )
    await Users.filter(id=user.id).update(expired_at=today - timedelta(days=1))

    user_fresh = await Users.get(id=user.id)
    resumed = await resume_frozen_base_if_due(user_fresh)
    assert resumed is True

    user_after_resume = await Users.get(id=user.id)
    freeze_after = await SubscriptionFreezes.get(id=freeze.id)

    assert freeze_after.resume_applied is True
    assert freeze_after.is_active is False
    assert user_after_resume.expired_at == today + timedelta(days=base_remaining_days)


@pytest.mark.asyncio
async def test_resume_stale_family_expiry_self_heals_without_rollback(monkeypatch):
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services import subscription_overlay as overlay_module
    from bloobcat.services.subscription_overlay import resume_frozen_base_if_due

    today = date.today()
    user = await Users.create(
        id=9151,
        username="u9151",
        full_name="User 9151",
        is_registered=True,
        expired_at=today + timedelta(days=45),
        hwid_limit=10,
    )
    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=14,
        base_expires_at_snapshot=today + timedelta(days=14),
        family_expires_at=today - timedelta(days=1),
        base_tariff_name="base",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    sent = {"user": 0, "admin": 0}

    async def fake_user_notify(*args, **kwargs):
        _ = args, kwargs
        sent["user"] += 1

    async def fake_admin_notify(*args, **kwargs):
        _ = args, kwargs
        sent["admin"] += 1

    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_success",
        fake_user_notify,
        raising=False,
    )
    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_admin",
        fake_admin_notify,
        raising=False,
    )

    resumed = await resume_frozen_base_if_due(user)
    assert resumed is False

    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    user_after = await Users.get(id=user.id)

    assert freeze_after.is_active is True
    assert freeze_after.resume_applied is False
    assert freeze_after.family_expires_at == user_after.expired_at
    assert (
        freeze_after.last_resume_error == "family_expiry_resynced_from_user_expired_at"
    )
    assert int(freeze_after.resume_attempt_count or 0) == 0
    assert sent == {"user": 0, "admin": 0}


@pytest.mark.asyncio
async def test_resume_notifications_retry_after_failed_first_send(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services import subscription_overlay as overlay_module
    from bloobcat.services.subscription_overlay import resume_frozen_base_if_due

    today = date.today()
    user = await Users.create(
        id=9401,
        username="u9401",
        full_name="User 9401",
        is_registered=True,
        expired_at=today - timedelta(days=1),
        hwid_limit=10,
    )
    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=14,
        base_expires_at_snapshot=today + timedelta(days=14),
        family_expires_at=today - timedelta(days=1),
        base_tariff_name="base_1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
    )

    sent = {"user": 0, "admin": 0}
    user_failures = {"remaining": 1}

    async def fake_user_notify(*args, **kwargs):
        _ = args, kwargs
        if user_failures["remaining"] > 0:
            user_failures["remaining"] -= 1
            raise RuntimeError("transient user send failure")
        sent["user"] += 1

    async def fake_admin_notify(*args, **kwargs):
        _ = args, kwargs
        sent["admin"] += 1

    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_success",
        fake_user_notify,
        raising=False,
    )
    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_admin",
        fake_admin_notify,
        raising=False,
    )

    first = await resume_frozen_base_if_due(await Users.get(id=user.id))
    second = await resume_frozen_base_if_due(await Users.get(id=user.id))
    third = await resume_frozen_base_if_due(await Users.get(id=user.id), force=True)

    assert first is True
    assert second is False
    assert third is False
    assert sent == {"user": 1, "admin": 1}

    marks = await NotificationMarks.filter(
        user_id=user.id,
        type="subscription_resume_notify",
    ).order_by("key")
    assert [(mark.key, mark.meta) for mark in marks] == [
        ("admin", f"freeze:{freeze.id}"),
        ("user", f"freeze:{freeze.id}"),
    ]


@pytest.mark.asyncio
async def test_resume_notifications_atomic_claim_blocks_reentrant_duplicate_send(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users
    from bloobcat.services import subscription_overlay as overlay_module

    today = date.today()
    freeze_id = 9501
    user = await Users.create(
        id=9501,
        username="u9501",
        full_name="User 9501",
        is_registered=True,
        expired_at=today + timedelta(days=14),
        hwid_limit=3,
    )

    sent = {"user": 0, "admin": 0}
    reentered = {"done": False}

    async def fake_user_notify(*args, **kwargs):
        _ = args, kwargs
        sent["user"] += 1
        if not reentered["done"]:
            reentered["done"] = True
            await overlay_module._notify_frozen_base_auto_resumed_once(
                user_id=user.id,
                freeze_id=freeze_id,
                restored_days=14,
                restored_until=today + timedelta(days=14),
            )

    async def fake_admin_notify(*args, **kwargs):
        _ = args, kwargs
        sent["admin"] += 1

    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_success",
        fake_user_notify,
        raising=False,
    )
    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_admin",
        fake_admin_notify,
        raising=False,
    )

    await overlay_module._notify_frozen_base_auto_resumed_once(
        user_id=user.id,
        freeze_id=freeze_id,
        restored_days=14,
        restored_until=today + timedelta(days=14),
    )

    assert sent == {"user": 1, "admin": 1}
    marks = await NotificationMarks.filter(
        user_id=user.id,
        type="subscription_resume_notify",
    ).order_by("key")
    assert [(mark.key, mark.meta) for mark in marks] == [
        ("admin", f"freeze:{freeze_id}"),
        ("user", f"freeze:{freeze_id}"),
    ]


@pytest.mark.asyncio
async def test_resume_notifications_reclaim_stale_pending_mark_and_complete_send(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users
    from bloobcat.services import subscription_overlay as overlay_module

    today = date.today()
    freeze_id = 9502
    user = await Users.create(
        id=9502,
        username="u9502",
        full_name="User 9502",
        is_registered=True,
        expired_at=today + timedelta(days=14),
        hwid_limit=3,
    )

    await NotificationMarks.create(
        user_id=user.id,
        type="subscription_resume_notify",
        key="user",
        meta=f"freeze:{freeze_id}:pending",
    )
    await NotificationMarks.filter(
        user_id=user.id,
        type="subscription_resume_notify",
        key="user",
        meta=f"freeze:{freeze_id}:pending",
    ).update(sent_at=datetime.now(timezone.utc) - timedelta(minutes=10))

    sent = {"user": 0, "admin": 0}

    async def fake_user_notify(*args, **kwargs):
        _ = args, kwargs
        sent["user"] += 1

    async def fake_admin_notify(*args, **kwargs):
        _ = args, kwargs
        sent["admin"] += 1

    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_success",
        fake_user_notify,
        raising=False,
    )
    monkeypatch.setattr(
        overlay_module,
        "notify_frozen_base_auto_resumed_admin",
        fake_admin_notify,
        raising=False,
    )

    await overlay_module._notify_frozen_base_auto_resumed_once(
        user_id=user.id,
        freeze_id=freeze_id,
        restored_days=14,
        restored_until=today + timedelta(days=14),
    )

    assert sent == {"user": 1, "admin": 1}
    marks = await NotificationMarks.filter(
        user_id=user.id,
        type="subscription_resume_notify",
    ).order_by("key", "meta")
    assert [(mark.key, mark.meta) for mark in marks] == [
        ("admin", f"freeze:{freeze_id}"),
        ("user", f"freeze:{freeze_id}"),
    ]


@pytest.mark.asyncio
async def test_resume_failure_rolls_back_partial_mutations(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services.subscription_overlay import resume_frozen_base_if_due

    today = date.today()
    user = await Users.create(
        id=9201,
        username="u9201",
        full_name="User 9201",
        is_registered=True,
        expired_at=today - timedelta(days=1),
        hwid_limit=10,
        lte_gb_total=0,
    )
    old_active = await ActiveTariffs.create(
        user=user,
        name="family_12m",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = old_active.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=20,
        base_expires_at_snapshot=today + timedelta(days=20),
        family_expires_at=today - timedelta(days=1),
        base_tariff_name="base",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    async def _raise_on_create(*args, **kwargs):
        raise RuntimeError("simulated create failure")

    monkeypatch.setattr(ActiveTariffs, "create", _raise_on_create)

    resumed = await resume_frozen_base_if_due(await Users.get(id=user.id))
    assert resumed is False

    old_active_after = await ActiveTariffs.get_or_none(id=old_active.id)
    user_after = await Users.get(id=user.id)
    freeze_after = await SubscriptionFreezes.get(id=freeze.id)

    assert old_active_after is not None
    assert int(user_after.active_tariff_id) == int(old_active.id)
    assert user_after.expired_at == today - timedelta(days=1)
    assert int(user_after.hwid_limit) == 10

    assert freeze_after.is_active is True
    assert freeze_after.resume_applied is False
    assert freeze_after.resumed_at is None
    assert int(freeze_after.resume_attempt_count) == 1
    assert freeze_after.last_resume_error
    assert "simulated create failure" in freeze_after.last_resume_error


@pytest.mark.asyncio
async def test_base_topup_during_active_family_updates_frozen_days_and_overlay_limit():
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services.subscription_overlay import (
        apply_base_purchase_to_frozen_base_if_active,
        get_overlay_payload,
    )

    today = date.today()
    family_expired_at = today + timedelta(days=120)
    user = await Users.create(
        id=9301,
        username="u9301",
        full_name="User 9301",
        is_registered=True,
        expired_at=family_expired_at,
        hwid_limit=10,
    )

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=15,
        base_expires_at_snapshot=today + timedelta(days=15),
        family_expires_at=family_expired_at,
        base_hwid_limit=3,
    )

    updated = await apply_base_purchase_to_frozen_base_if_active(
        user, purchased_days=30
    )
    assert updated is True

    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    assert int(freeze_after.base_remaining_days or 0) == 45
    assert freeze_after.base_expires_at_snapshot == today + timedelta(days=45)

    user_after = await Users.get(id=user.id)
    assert user_after.expired_at == family_expired_at

    overlay = await get_overlay_payload(user_after)
    assert overlay["has_frozen_base"] is True
    assert int(overlay["base_hwid_limit"]) == 3

    freeze_after_overlay = await SubscriptionFreezes.get(id=freeze.id)
    assert freeze_after_overlay.is_active is True
    assert freeze_after_overlay.resume_applied is False


@pytest.mark.asyncio
async def test_base_purchase_during_family_refreshes_stale_trial_snapshot_before_resume():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services.subscription_overlay import (
        apply_base_purchase_to_frozen_base_if_active,
        resume_frozen_base_if_due,
    )

    today = date.today()
    family_expired_at = today + timedelta(days=120)
    user = await Users.create(
        id=9331,
        username="u9331",
        full_name="User 9331",
        is_registered=True,
        expired_at=family_expired_at,
        hwid_limit=10,
        lte_gb_total=0,
    )

    family_active = await ActiveTariffs.create(
        user=user,
        name="family_12m",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = family_active.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=3,
        base_expires_at_snapshot=today + timedelta(days=3),
        family_expires_at=family_expired_at,
        base_hwid_limit=1,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.0,
        base_residual_day_fraction=0.0,
    )

    updated = await apply_base_purchase_to_frozen_base_if_active(
        user,
        purchased_days=30,
        base_tariff_snapshot={
            "base_tariff_name": "base_1m",
            "base_tariff_months": 1,
            "base_tariff_price": 1000,
            "base_hwid_limit": 3,
            "base_lte_gb_total": 5,
            "base_lte_gb_used": 0.0,
            "base_lte_price_per_gb": 12.5,
            "base_progressive_multiplier": 0.9,
            "base_residual_day_fraction": 0.0,
        },
    )
    assert updated is True

    freeze_after_purchase = await SubscriptionFreezes.get(id=freeze.id)
    assert int(freeze_after_purchase.base_remaining_days or 0) == 33
    assert freeze_after_purchase.base_expires_at_snapshot == today + timedelta(days=33)
    assert freeze_after_purchase.base_tariff_name == "base_1m"
    assert int(freeze_after_purchase.base_tariff_months or 0) == 1
    assert int(freeze_after_purchase.base_tariff_price or 0) == 1000
    assert int(freeze_after_purchase.base_hwid_limit or 0) == 3
    assert int(freeze_after_purchase.base_lte_gb_total or 0) == 5
    assert float(freeze_after_purchase.base_lte_price_per_gb or 0.0) == 12.5
    assert float(freeze_after_purchase.base_progressive_multiplier or 0.0) == 0.9

    await SubscriptionFreezes.filter(id=freeze.id).update(
        family_expires_at=today - timedelta(days=1)
    )
    await Users.filter(id=user.id).update(expired_at=today - timedelta(days=1))

    resumed = await resume_frozen_base_if_due(await Users.get(id=user.id))
    assert resumed is True

    user_after_resume = await Users.get(id=user.id)
    restored_tariff = await ActiveTariffs.get(id=user_after_resume.active_tariff_id)

    assert user_after_resume.expired_at == today + timedelta(days=33)
    assert int(user_after_resume.hwid_limit or 0) == 3
    assert int(user_after_resume.lte_gb_total or 0) == 5
    assert restored_tariff.name == "base_1m"
    assert int(restored_tariff.months) == 1
    assert int(restored_tariff.price) == 1000
    assert int(restored_tariff.hwid_limit) == 3
    assert int(restored_tariff.lte_gb_total or 0) == 5


@pytest.mark.asyncio
async def test_activate_frozen_base_switches_now_and_freezes_current_family_period():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services.subscription_overlay import (
        FREEZE_REASON_BASE_OVERLAY,
        activate_frozen_base_with_current_freeze,
        get_overlay_payload,
        has_active_family_overlay,
    )

    today = date.today()
    user = await Users.create(
        id=9341,
        username="u9341",
        full_name="User 9341",
        is_registered=True,
        expired_at=today + timedelta(days=120),
        hwid_limit=10,
        lte_gb_total=0,
    )
    family_active = await ActiveTariffs.create(
        user=user,
        name="family_12m",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = family_active.id
    await user.save(update_fields=["active_tariff_id"])

    previous_freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=14,
        base_expires_at_snapshot=today + timedelta(days=14),
        family_expires_at=today + timedelta(days=120),
        base_tariff_name="base_1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    result = await activate_frozen_base_with_current_freeze(await Users.get(id=user.id))

    assert int(result["activated_frozen_base_days"]) == 14
    assert int(result["frozen_current_days"]) == 120
    assert str(result["switched_until"]) == (today + timedelta(days=14)).isoformat()

    user_after = await Users.get(id=user.id)
    assert user_after.expired_at == today + timedelta(days=14)
    assert int(user_after.hwid_limit or 0) == 3

    active_after = await ActiveTariffs.get_or_none(id=user_after.active_tariff_id)
    assert active_after is not None
    assert active_after.name == "base_1m"
    assert int(active_after.hwid_limit) == 3

    previous_freeze_after = await SubscriptionFreezes.get(id=previous_freeze.id)
    assert previous_freeze_after.is_active is False
    assert previous_freeze_after.resume_applied is True

    reverse_freeze = await SubscriptionFreezes.get(
        user_id=user.id, is_active=True, resume_applied=False
    )
    assert reverse_freeze.freeze_reason == FREEZE_REASON_BASE_OVERLAY
    assert int(reverse_freeze.base_remaining_days or 0) == 120
    assert reverse_freeze.family_expires_at == today + timedelta(days=14)
    assert reverse_freeze.base_tariff_name == "family_12m"
    assert int(reverse_freeze.base_hwid_limit or 0) == 10

    overlay = await get_overlay_payload(user_after)
    assert overlay["has_frozen_base"] is False
    assert overlay["active_kind"] == "base"

    assert await has_active_family_overlay(user_after) is False


@pytest.mark.asyncio
async def test_activate_frozen_base_endpoint_returns_success_payload(monkeypatch):
    import httpx
    from fastapi import FastAPI

    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.funcs.validate import validate
    from bloobcat.routes import subscription as subscription_module
    from bloobcat.routes.subscription import router as subscription_router

    today = date.today()
    user = await Users.create(
        id=9342,
        username="u9342",
        full_name="User 9342",
        is_registered=True,
        expired_at=today + timedelta(days=45),
        hwid_limit=10,
        lte_gb_total=0,
    )
    family_active = await ActiveTariffs.create(
        user=user,
        name="family_12m",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = family_active.id
    await user.save(update_fields=["active_tariff_id"])

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=9,
        base_expires_at_snapshot=today + timedelta(days=9),
        family_expires_at=today + timedelta(days=45),
        base_tariff_name="base_1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    app = FastAPI()
    app.include_router(subscription_router)

    calls: list[int] = []

    async def fake_notify(user_arg, result_arg):
        assert user_arg.id == user.id
        assert int(result_arg["activated_frozen_base_days"]) == 9
        calls.append(user_arg.id)
        return None

    monkeypatch.setattr(
        subscription_module,
        "_notify_frozen_base_activation_success",
        fake_notify,
        raising=False,
    )

    async def _override_validate() -> Users:
        return await Users.get(id=user.id)

    app.dependency_overrides[validate] = _override_validate

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/subscription/frozen-base/activate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert int(payload["activated_frozen_base_days"]) == 9
    assert int(payload["frozen_current_days"]) == 45
    assert payload["switched_until"] == (today + timedelta(days=9)).isoformat()

    reverse_freeze = await SubscriptionFreezes.get(
        user_id=user.id,
        freeze_reason="base_overlay",
        is_active=True,
        resume_applied=False,
    )
    assert int(reverse_freeze.base_remaining_days or 0) == 45
    assert calls == [user.id]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_activate_frozen_base_endpoint_returns_conflict_when_missing_overlay(
    monkeypatch,
):
    import httpx
    from fastapi import FastAPI

    from bloobcat.db.users import Users
    from bloobcat.funcs.validate import validate
    from bloobcat.routes import subscription as subscription_module
    from bloobcat.routes.subscription import router as subscription_router

    today = date.today()
    user = await Users.create(
        id=9343,
        username="u9343",
        full_name="User 9343",
        is_registered=True,
        expired_at=today + timedelta(days=15),
        hwid_limit=3,
        lte_gb_total=0,
    )

    app = FastAPI()
    app.include_router(subscription_router)

    calls: list[str] = []

    async def fake_notify(*args, **kwargs):
        _ = args, kwargs
        calls.append("called")
        return None

    monkeypatch.setattr(
        subscription_module,
        "_notify_frozen_base_activation_success",
        fake_notify,
        raising=False,
    )

    async def _override_validate() -> Users:
        return await Users.get(id=user.id)

    app.dependency_overrides[validate] = _override_validate

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/subscription/frozen-base/activate")

    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "FROZEN_BASE_NOT_FOUND"
    assert calls == []

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_activate_frozen_base_endpoint_returns_conflict_when_frozen_days_empty():
    import httpx
    from fastapi import FastAPI

    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.funcs.validate import validate
    from bloobcat.routes.subscription import router as subscription_router

    today = date.today()
    user = await Users.create(
        id=9344,
        username="u9344",
        full_name="User 9344",
        is_registered=True,
        expired_at=today + timedelta(days=20),
        hwid_limit=10,
        lte_gb_total=0,
    )

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=0,
        base_expires_at_snapshot=today,
        family_expires_at=today + timedelta(days=20),
        base_hwid_limit=3,
    )

    app = FastAPI()
    app.include_router(subscription_router)

    async def _override_validate() -> Users:
        return await Users.get(id=user.id)

    app.dependency_overrides[validate] = _override_validate

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/subscription/frozen-base/activate")

    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "FROZEN_BASE_EMPTY"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_activate_frozen_base_endpoint_returns_conflict_when_overlay_expired():
    import httpx
    from fastapi import FastAPI

    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.funcs.validate import validate
    from bloobcat.routes.subscription import router as subscription_router

    today = date.today()
    user = await Users.create(
        id=9345,
        username="u9345",
        full_name="User 9345",
        is_registered=True,
        expired_at=today + timedelta(days=20),
        hwid_limit=10,
        lte_gb_total=0,
    )

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=7,
        base_expires_at_snapshot=today + timedelta(days=7),
        family_expires_at=today - timedelta(days=1),
        base_hwid_limit=3,
    )

    app = FastAPI()
    app.include_router(subscription_router)

    async def _override_validate() -> Users:
        return await Users.get(id=user.id)

    app.dependency_overrides[validate] = _override_validate

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/subscription/frozen-base/activate")

    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "FROZEN_BASE_EXPIRED"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_activate_frozen_family_endpoint_returns_cooldown_conflict(monkeypatch):
    import httpx
    from fastapi import FastAPI

    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.funcs.validate import validate
    from bloobcat.routes import subscription as subscription_module
    from bloobcat.routes.subscription import router as subscription_router
    from bloobcat.services import subscription_overlay as overlay_module

    today = date.today()
    user = await Users.create(
        id=9346,
        username="u9346",
        full_name="User 9346",
        is_registered=True,
        expired_at=today + timedelta(days=14),
        hwid_limit=3,
        lte_gb_total=0,
    )
    base_active = await ActiveTariffs.create(
        user=user,
        name="base_1m",
        months=1,
        price=1000,
        hwid_limit=3,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = base_active.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="base_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=120,
        base_expires_at_snapshot=today + timedelta(days=120),
        family_expires_at=today + timedelta(days=14),
        base_tariff_name="family_12m",
        base_tariff_months=12,
        base_tariff_price=4490,
        base_hwid_limit=10,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    created_at = freeze.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    available_at = created_at + timedelta(
        seconds=overlay_module.REVERSE_MIGRATION_COOLDOWN_SECONDS
    )

    app = FastAPI()
    app.include_router(subscription_router)

    calls: list[str] = []

    async def fake_notify(*args, **kwargs):
        _ = args, kwargs
        calls.append("called")
        return None

    monkeypatch.setattr(
        subscription_module,
        "_notify_frozen_family_activation_success",
        fake_notify,
        raising=False,
    )

    async def _override_validate() -> Users:
        return await Users.get(id=user.id)

    app.dependency_overrides[validate] = _override_validate

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/subscription/frozen-family/activate")

    assert response.status_code == 409
    payload = response.json()
    assert payload["detail"]["code"] == "FAMILY_RESTORE_COOLDOWN_ACTIVE"
    assert int(payload["detail"]["retry_after_seconds"]) > 0
    assert (
        payload["detail"]["reverse_migration_available_at"] == available_at.isoformat()
    )
    assert calls == []

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_activate_frozen_family_endpoint_switches_after_cooldown(monkeypatch):
    import httpx
    from fastapi import FastAPI

    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.funcs.validate import validate
    from bloobcat.routes import subscription as subscription_module
    from bloobcat.routes.subscription import router as subscription_router
    from bloobcat.services import subscription_overlay as overlay_module

    today = date.today()
    user = await Users.create(
        id=9347,
        username="u9347",
        full_name="User 9347",
        is_registered=True,
        expired_at=today + timedelta(days=14),
        hwid_limit=3,
        lte_gb_total=0,
    )
    base_active = await ActiveTariffs.create(
        user=user,
        name="base_1m",
        months=1,
        price=1000,
        hwid_limit=3,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = base_active.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="base_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=120,
        base_expires_at_snapshot=today + timedelta(days=120),
        family_expires_at=today + timedelta(days=14),
        base_tariff_name="family_12m",
        base_tariff_months=12,
        base_tariff_price=4490,
        base_hwid_limit=10,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    old_created = freeze.created_at - timedelta(
        seconds=overlay_module.REVERSE_MIGRATION_COOLDOWN_SECONDS + 5
    )
    await SubscriptionFreezes.filter(id=freeze.id).update(created_at=old_created)

    app = FastAPI()
    app.include_router(subscription_router)

    calls: list[int] = []

    async def fake_notify(user_arg, result_arg):
        assert user_arg.id == user.id
        assert int(result_arg["activated_frozen_family_days"]) == 120
        calls.append(user_arg.id)
        return None

    monkeypatch.setattr(
        subscription_module,
        "_notify_frozen_family_activation_success",
        fake_notify,
        raising=False,
    )

    async def _override_validate() -> Users:
        return await Users.get(id=user.id)

    app.dependency_overrides[validate] = _override_validate

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/subscription/frozen-family/activate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert int(payload["activated_frozen_family_days"]) == 120
    assert int(payload["frozen_current_days"]) == 14

    user_after = await Users.get(id=user.id)
    assert user_after.expired_at == today + timedelta(days=120)
    assert int(user_after.hwid_limit or 0) == 10

    active_freezes = await SubscriptionFreezes.filter(
        user_id=user.id, is_active=True, resume_applied=False
    ).order_by("id")
    assert len(active_freezes) == 1
    assert active_freezes[0].freeze_reason == "family_overlay"
    assert int(active_freezes[0].base_remaining_days or 0) == 14
    assert calls == [user.id]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cancel_renewal_endpoint_notifies_admin_only_when_state_changes(
    monkeypatch,
):
    import httpx
    from fastapi import FastAPI

    from bloobcat.db.users import Users
    from bloobcat.funcs.validate import validate
    from bloobcat.routes import subscription as subscription_module
    from bloobcat.routes.subscription import router as subscription_router

    user = await Users.create(
        id=9381,
        username="u9381",
        full_name="User 9381",
        is_registered=True,
        renew_id="renew_9381",
        is_subscribed=True,
    )

    admin_calls: list[tuple[int, str]] = []

    async def fake_cancel_subscription(user_arg, reason=""):
        admin_calls.append((user_arg.id, reason))
        return None

    monkeypatch.setattr(
        subscription_module,
        "cancel_subscription",
        fake_cancel_subscription,
        raising=False,
    )

    app = FastAPI()
    app.include_router(subscription_router)

    async def _override_validate() -> Users:
        return await Users.get(id=user.id)

    app.dependency_overrides[validate] = _override_validate

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        first = await client.post("/subscription/cancel-renewal")
        second = await client.post("/subscription/cancel-renewal")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["wasAlreadyCancelled"] is False
    assert second.json()["wasAlreadyCancelled"] is True
    assert admin_calls == [
        (
            user.id,
            "Пользователь отключил автопродление через /subscription/cancel-renewal",
        )
    ]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_overlay_suppresses_obviously_stale_trial_freeze_state():
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services.subscription_overlay import get_overlay_payload

    today = date.today()
    user = await Users.create(
        id=9351,
        username="u9351",
        full_name="User 9351",
        is_registered=False,
        expired_at=today - timedelta(days=1),
        hwid_limit=1,
    )

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=12,
        base_expires_at_snapshot=today + timedelta(days=12),
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    overlay = await get_overlay_payload(user)
    assert overlay["has_frozen_base"] is False
    assert overlay["active_kind"] == "base"

    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    assert freeze_after.is_active is True
    assert freeze_after.resume_applied is False
    assert freeze_after.last_resume_error is None


@pytest.mark.asyncio
async def test_overlay_keeps_frozen_base_for_active_paid_family_even_if_user_not_registered():
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services.subscription_overlay import get_overlay_payload

    today = date.today()
    user = await Users.create(
        id=9352,
        username="u9352",
        full_name="User 9352",
        is_registered=False,
        expired_at=today + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=10,
    )

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=12,
        base_expires_at_snapshot=today + timedelta(days=12),
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    overlay = await get_overlay_payload(user)

    assert overlay["has_frozen_base"] is True
    assert overlay["active_kind"] == "family"
    assert overlay["will_restore_base_after_family"] is True
    assert int(overlay["base_remaining_days"]) == 12
    assert int(overlay["base_hwid_limit"]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("base_remaining_days", [0, -3])
async def test_overlay_suppresses_freeze_when_base_remaining_days_not_positive(
    base_remaining_days, monkeypatch
):
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services import subscription_overlay as overlay_module

    today = date.today()
    user = await Users.create(
        id=9360 + abs(int(base_remaining_days)),
        username=f"u9360-{base_remaining_days}",
        full_name="User 9360",
        is_registered=True,
        expired_at=today + timedelta(days=30),
        hwid_limit=10,
    )

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=int(base_remaining_days),
        base_expires_at_snapshot=today,
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    warnings: list[tuple] = []

    def _capture_warning(*args, **kwargs):
        _ = kwargs
        warnings.append(args)

    monkeypatch.setattr(overlay_module.logger, "warning", _capture_warning)

    payload = await overlay_module.get_overlay_payload(user)

    assert payload["has_frozen_base"] is False
    assert payload["active_kind"] == "family"
    assert "base_remaining_days" not in payload
    assert len(warnings) == 1
    assert "stale_freeze_non_positive_base_remaining_days" in warnings[0][3]


@pytest.mark.asyncio
async def test_fallback_family_renewal_extends_active_overlay_without_shortening():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback
    from bloobcat.utils.dates import add_months_safe

    today = date.today()
    existing_family_expiry = today + timedelta(days=120)
    purchased_days = (add_months_safe(today, 12) - today).days

    user = await Users.create(
        id=9401,
        username="u9401",
        full_name="User 9401",
        is_registered=True,
        expired_at=existing_family_expiry,
        hwid_limit=10,
    )
    family_active = await ActiveTariffs.create(
        user=user,
        name="family",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = family_active.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=20,
        base_expires_at_snapshot=today + timedelta(days=20),
        family_expires_at=existing_family_expiry,
        base_hwid_limit=3,
    )

    tariff = await Tariffs.create(
        id=9402,
        name="family_12m",
        months=12,
        base_price=4490,
        progressive_multiplier=0.9,
        order=1,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    yk_payment = types.SimpleNamespace(
        id="family-renew-fallback-9401",
        amount=types.SimpleNamespace(value="4490.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 12,
        "tariff_id": tariff.id,
        "device_count": 10,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    applied = await _apply_succeeded_payment_fallback(yk_payment, user, metadata)
    assert applied is True

    user_after = await Users.get(id=user.id)
    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    expected_expiry = existing_family_expiry + timedelta(days=purchased_days)

    assert user_after.expired_at == expected_expiry
    assert user_after.expired_at >= existing_family_expiry
    assert freeze_after.family_expires_at == expected_expiry


@pytest.mark.asyncio
async def test_family_repurchase_while_base_overlay_active_supersedes_old_overlay_and_restores_base():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback
    from bloobcat.services.subscription_overlay import (
        FREEZE_REASON_BASE_OVERLAY,
        FREEZE_REASON_FAMILY_OVERLAY,
        activate_frozen_base_with_current_freeze,
        resume_frozen_base_if_due,
    )
    from bloobcat.utils.dates import add_months_safe

    today = date.today()
    frozen_base_days = 14
    frozen_family_days = 120
    purchased_days = (add_months_safe(today, 12) - today).days

    user = await Users.create(
        id=9403,
        username="u9403",
        full_name="User 9403",
        is_registered=True,
        expired_at=today + timedelta(days=frozen_family_days),
        hwid_limit=10,
        lte_gb_total=0,
    )
    family_active = await ActiveTariffs.create(
        user=user,
        name="family_12m",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = family_active.id
    await user.save(update_fields=["active_tariff_id"])

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason=FREEZE_REASON_FAMILY_OVERLAY,
        is_active=True,
        resume_applied=False,
        base_remaining_days=frozen_base_days,
        base_expires_at_snapshot=today + timedelta(days=frozen_base_days),
        family_expires_at=today + timedelta(days=frozen_family_days),
        base_tariff_name="base_1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    await activate_frozen_base_with_current_freeze(await Users.get(id=user.id))

    user_on_base = await Users.get(id=user.id)
    original_base_overlay = await SubscriptionFreezes.get(
        user_id=user.id,
        freeze_reason=FREEZE_REASON_BASE_OVERLAY,
        is_active=True,
        resume_applied=False,
    )
    assert int(original_base_overlay.base_remaining_days or 0) == frozen_family_days

    tariff = await Tariffs.create(
        id=9404,
        name="family_12m_reentry",
        months=12,
        base_price=4490,
        progressive_multiplier=0.9,
        order=2,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    yk_payment = types.SimpleNamespace(
        id="family-reentry-fallback-9403",
        amount=types.SimpleNamespace(value="4490.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 12,
        "tariff_id": tariff.id,
        "device_count": 10,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    applied = await _apply_succeeded_payment_fallback(
        yk_payment, user_on_base, metadata
    )
    assert applied is True

    user_after_purchase = await Users.get(id=user.id)
    active_freezes = await SubscriptionFreezes.filter(
        user_id=user.id,
        is_active=True,
        resume_applied=False,
    ).order_by("id")
    assert len(active_freezes) == 1

    active_freeze = active_freezes[0]
    expected_family_expiry = today + timedelta(days=frozen_family_days + purchased_days)
    assert active_freeze.freeze_reason == FREEZE_REASON_FAMILY_OVERLAY
    assert active_freeze.family_expires_at == expected_family_expiry
    assert int(active_freeze.base_remaining_days or 0) == frozen_base_days
    assert active_freeze.base_expires_at_snapshot == today + timedelta(
        days=frozen_base_days
    )
    assert active_freeze.base_tariff_name == "base_1m"

    superseded_base_overlay = await SubscriptionFreezes.get(id=original_base_overlay.id)
    assert superseded_base_overlay.is_active is False
    assert superseded_base_overlay.resume_applied is False
    assert superseded_base_overlay.last_resume_error == "superseded_by_family_purchase"

    assert user_after_purchase.expired_at == expected_family_expiry
    assert int(user_after_purchase.hwid_limit or 0) == 10

    await Users.filter(id=user.id).update(expired_at=today - timedelta(days=1))
    await SubscriptionFreezes.filter(id=active_freeze.id).update(
        family_expires_at=today - timedelta(days=1)
    )

    resumed = await resume_frozen_base_if_due(await Users.get(id=user.id))
    assert resumed is True

    user_after_resume = await Users.get(id=user.id)
    resumed_freeze = await SubscriptionFreezes.get(id=active_freeze.id)
    restored_tariff = await ActiveTariffs.get(id=user_after_resume.active_tariff_id)

    assert resumed_freeze.is_active is False
    assert resumed_freeze.resume_applied is True
    assert user_after_resume.expired_at == today + timedelta(days=frozen_base_days)
    assert int(user_after_resume.hwid_limit or 0) == 3
    assert restored_tariff.name == "base_1m"


@pytest.fixture
def _reset_overlay_warning_throttle_cache():
    from bloobcat.services import subscription_overlay as overlay_module

    overlay_module._stale_warning_cache.clear()
    try:
        yield overlay_module
    finally:
        overlay_module._stale_warning_cache.clear()


@pytest.mark.asyncio
async def test_overlay_stale_warning_throttled_for_same_key_within_window(
    monkeypatch, _reset_overlay_warning_throttle_cache
):
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services import subscription_overlay as overlay_module

    today = date.today()
    user = await Users.create(
        id=9501,
        username="u9501",
        full_name="User 9501",
        is_registered=False,
        expired_at=today - timedelta(days=1),
        hwid_limit=1,
    )
    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=12,
        base_expires_at_snapshot=today + timedelta(days=12),
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    warnings: list[tuple] = []
    clock = {"value": 1000.0}

    def _capture_warning(*args, **kwargs):
        warnings.append(args)

    monkeypatch.setattr(overlay_module.logger, "warning", _capture_warning)
    monkeypatch.setattr(overlay_module, "_now_monotonic", lambda: clock["value"])

    payload_first = await overlay_module.get_overlay_payload(user)
    payload_second = await overlay_module.get_overlay_payload(user)

    assert payload_first["has_frozen_base"] is False
    assert payload_second["has_frozen_base"] is False
    assert len(warnings) == 1
    assert "stale_freeze_user_not_active" in warnings[0][3]


@pytest.mark.asyncio
async def test_overlay_stale_warning_emits_again_when_reason_changes(
    monkeypatch, _reset_overlay_warning_throttle_cache
):
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services import subscription_overlay as overlay_module

    today = date.today()
    user = await Users.create(
        id=9502,
        username="u9502",
        full_name="User 9502",
        is_registered=False,
        expired_at=today - timedelta(days=1),
        hwid_limit=1,
    )
    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=12,
        base_expires_at_snapshot=today + timedelta(days=12),
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    warnings: list[tuple] = []
    clock = {"value": 2000.0}

    def _capture_warning(*args, **kwargs):
        warnings.append(args)

    monkeypatch.setattr(overlay_module.logger, "warning", _capture_warning)
    monkeypatch.setattr(overlay_module, "_now_monotonic", lambda: clock["value"])

    first_payload = await overlay_module.get_overlay_payload(user)
    assert first_payload["has_frozen_base"] is False
    assert len(warnings) == 1
    assert "stale_freeze_user_not_active" in warnings[0][3]

    await Users.filter(id=user.id).update(expired_at=today + timedelta(days=10))
    await SubscriptionFreezes.filter(user_id=user.id).update(base_remaining_days=0)
    user_after_update = await Users.get(id=user.id)
    second_payload = await overlay_module.get_overlay_payload(user_after_update)
    assert second_payload["has_frozen_base"] is False
    assert len(warnings) == 2
    assert "stale_freeze_non_positive_base_remaining_days" in warnings[1][3]


def test_overlay_warning_throttle_cache_prunes_and_stays_bounded(
    monkeypatch, _reset_overlay_warning_throttle_cache
):
    from bloobcat.services import subscription_overlay as overlay_module

    monkeypatch.setattr(overlay_module, "STALE_WARNING_CACHE_MAX_SIZE", 2)
    monkeypatch.setattr(overlay_module, "STALE_WARNING_THROTTLE_SECONDS", 60.0)

    now = {"value": 3000.0}
    monkeypatch.setattr(overlay_module, "_now_monotonic", lambda: now["value"])

    assert (
        overlay_module._should_emit_stale_overlay_warning(
            user_id=1, freeze_id=1, reason="r1"
        )
        is True
    )
    now["value"] += 1
    assert (
        overlay_module._should_emit_stale_overlay_warning(
            user_id=2, freeze_id=2, reason="r1"
        )
        is True
    )
    now["value"] += 1
    assert (
        overlay_module._should_emit_stale_overlay_warning(
            user_id=3, freeze_id=3, reason="r1"
        )
        is True
    )

    keys = set(overlay_module._stale_warning_cache.keys())
    assert len(keys) == 2
    assert keys == {(2, 2, "r1"), (3, 3, "r1")}

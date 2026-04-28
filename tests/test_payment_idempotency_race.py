import asyncio
import types
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import HTTPException
from tortoise.exceptions import IntegrityError
from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
    from tests.test_payments_no_yookassa import install_stubs
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _sqlite_datetime_compat import register_sqlite_datetime_compat
    from test_payments_no_yookassa import install_stubs


register_sqlite_datetime_compat()


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest.fixture(autouse=True)
def _use_yookassa_provider_for_legacy_payment_tests(monkeypatch, _install_stubs_once):
    from pydantic import SecretStr

    from bloobcat.routes import payment as payment_module

    monkeypatch.setattr(payment_module.payment_settings, "provider", "yookassa")
    monkeypatch.setattr(payment_module.payment_settings, "auto_renewal_mode", "yookassa")
    monkeypatch.setattr(payment_module.yookassa_settings, "shop_id", "test-shop")
    monkeypatch.setattr(payment_module.yookassa_settings, "secret_key", SecretStr("test-secret"))
    monkeypatch.setattr(payment_module.yookassa_settings, "webhook_secret", "test-webhook-secret")
    payment_module._configure_yookassa_if_available()


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
async def test_fallback_apply_same_payment_id_is_idempotent():
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback

    user = await Users.create(
        id=9001, username="u9001", full_name="User 9001", is_registered=True
    )
    tariff = await Tariffs.create(
        id=9001,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    yk_payment = types.SimpleNamespace(
        id="idem-9001",
        amount=types.SimpleNamespace(value="1000.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 1,
        "tariff_id": tariff.id,
        "device_count": 3,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    first = await _apply_succeeded_payment_fallback(yk_payment, user, metadata)
    assert first is True
    user_after_first = await Users.get(id=user.id)
    first_expired_at = user_after_first.expired_at

    second = await _apply_succeeded_payment_fallback(
        yk_payment, user_after_first, metadata
    )
    assert second is True
    user_after_second = await Users.get(id=user.id)

    assert user_after_second.expired_at == first_expired_at
    assert (
        user_after_second.expired_at is not None
        and user_after_second.expired_at >= date.today()
    )

    row = await ProcessedPayments.get(payment_id="idem-9001")
    assert row.status == "succeeded"
    assert row.effect_applied is True
    assert row.processing_state == "applied"


@pytest.mark.asyncio
async def test_payment_status_fail_closed_when_metadata_has_no_owner(monkeypatch):
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9002, username="u9002", full_name="User 9002", is_registered=True
    )

    fake_payment = types.SimpleNamespace(
        id="foreign-without-meta-owner",
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_payment_status("foreign-without-meta-owner", user=user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_payment_status_allows_metadata_less_response_when_processed_row_belongs_to_user(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9010, username="u9010", full_name="User 9010", is_registered=True
    )

    await ProcessedPayments.create(
        payment_id="owner-proved-by-processed-row",
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="pending",
        processing_state="pending",
    )

    fake_payment = types.SimpleNamespace(
        id="owner-proved-by-processed-row",
        status="pending",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    result = await get_payment_status("owner-proved-by-processed-row", user=user)

    assert result["payment_id"] == "owner-proved-by-processed-row"
    assert result["processed"] is True
    assert result["processed_status"] == "pending"
    assert result["entitlements_ready"] is False


@pytest.mark.asyncio
async def test_payment_status_entitlements_ready_transitions_after_processing(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    payment_id = "entitlements-transition-9019"
    user = await Users.create(
        id=9019, username="u9019", full_name="User 9019", is_registered=True
    )

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="pending",
        processing_state="pending",
        effect_applied=False,
    )

    payment_state = {"status": "pending"}

    def fake_find_one(_pid: str):
        return types.SimpleNamespace(
            id=payment_id,
            status=payment_state["status"],
            amount=types.SimpleNamespace(value="100.00", currency="RUB"),
            metadata={"user_id": str(user.id)},
        )

    async def fake_apply_succeeded(*_args, **_kwargs):
        row = await ProcessedPayments.get(payment_id=payment_id)
        row.status = "succeeded"
        row.processing_state = "applied"
        row.effect_applied = True
        await row.save(update_fields=["status", "processing_state", "effect_applied"])
        return True

    monkeypatch.setattr(
        payment_module.Payment, "find_one", staticmethod(fake_find_one), raising=False
    )
    monkeypatch.setattr(
        payment_module,
        "_apply_succeeded_payment_fallback",
        fake_apply_succeeded,
        raising=False,
    )

    pending_result = await get_payment_status(payment_id, user=user)
    assert pending_result["is_paid"] is False
    assert pending_result["processed"] is True
    assert pending_result["processed_status"] == "pending"
    assert pending_result["entitlements_ready"] is False

    payment_state["status"] = "succeeded"

    ready_result = await get_payment_status(payment_id, user=user)
    assert ready_result["is_paid"] is True
    assert ready_result["processed"] is True
    assert ready_result["processed_status"] == "succeeded"
    assert ready_result["entitlements_ready"] is True


@pytest.mark.asyncio
async def test_payment_status_denies_metadata_less_response_when_processed_row_belongs_to_another_user(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    owner = await Users.create(
        id=9011, username="u9011", full_name="User 9011", is_registered=True
    )
    foreign_user = await Users.create(
        id=9012, username="u9012", full_name="User 9012", is_registered=True
    )

    await ProcessedPayments.create(
        payment_id="metadata-less-foreign-owner",
        user_id=owner.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="pending",
        processing_state="pending",
    )

    fake_payment = types.SimpleNamespace(
        id="metadata-less-foreign-owner",
        status="pending",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_payment_status("metadata-less-foreign-owner", user=foreign_user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_payment_status_fail_closed_when_metadata_owner_invalid_without_processed_row(
    monkeypatch,
):
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9003, username="u9003", full_name="User 9003", is_registered=True
    )

    fake_payment = types.SimpleNamespace(
        id="owner-without-meta-owner",
        status="pending",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={"user_id": "not-an-int"},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_payment_status("owner-without-meta-owner", user=user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_payment_status_denies_invalid_metadata_owner_even_with_processed_row_for_user(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9013, username="u9013", full_name="User 9013", is_registered=True
    )

    await ProcessedPayments.create(
        payment_id="invalid-meta-owner-present",
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="pending",
        processing_state="pending",
    )

    fake_payment = types.SimpleNamespace(
        id="invalid-meta-owner-present",
        status="pending",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={"user_id": "not-an-int"},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_payment_status("invalid-meta-owner-present", user=user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_processing_lease_refresh_blocks_stale_reclaim():
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.routes.payment import (
        PAYMENT_PROCESSING_STALE_SECONDS,
        _claim_payment_effect_once,
        _refresh_payment_processing_lease,
    )

    stale_ts = datetime.now(timezone.utc) - timedelta(
        seconds=PAYMENT_PROCESSING_STALE_SECONDS + 5
    )

    await ProcessedPayments.create(
        payment_id="stale-no-refresh",
        user_id=9101,
        amount=0,
        amount_external=0,
        amount_from_balance=0,
        status="pending",
        processing_state="processing",
        last_attempt_at=stale_ts,
        effect_applied=False,
    )
    claimed_without_refresh = await _claim_payment_effect_once(
        payment_id="stale-no-refresh",
        user_id=9102,
        source="webhook",
    )
    assert claimed_without_refresh is True

    await ProcessedPayments.create(
        payment_id="stale-with-refresh",
        user_id=9103,
        amount=0,
        amount_external=0,
        amount_from_balance=0,
        status="pending",
        processing_state="processing",
        last_attempt_at=stale_ts,
        effect_applied=False,
    )
    await _refresh_payment_processing_lease(
        payment_id="stale-with-refresh",
        user_id=9103,
        source="webhook",
    )
    claimed_with_refresh = await _claim_payment_effect_once(
        payment_id="stale-with-refresh",
        user_id=9104,
        source="fallback",
    )
    assert claimed_with_refresh is False


@pytest.mark.asyncio
async def test_claim_payment_effect_once_handles_naive_last_attempt_at():
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.routes.payment import _claim_payment_effect_once

    naive_recent = datetime.now(timezone.utc).replace(tzinfo=None)
    await ProcessedPayments.create(
        payment_id="naive-last-attempt",
        user_id=9105,
        amount=0,
        amount_external=0,
        amount_from_balance=0,
        status="pending",
        processing_state="processing",
        last_attempt_at=naive_recent,
        effect_applied=False,
    )

    claimed = await _claim_payment_effect_once(
        payment_id="naive-last-attempt",
        user_id=9106,
        source="fallback",
    )

    assert claimed is False


@pytest.mark.asyncio
async def test_replay_user_notification_failure_is_logged(monkeypatch):
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _replay_payment_notifications_if_needed
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9301, username="u9301", full_name="User 9301", is_registered=True
    )

    observed: list[dict[str, Any]] = []

    async def failing_user_notify(*args, **kwargs):
        _ = args, kwargs
        raise RuntimeError("user replay notify failure")

    async def noop_admin_notify(*args, **kwargs):
        _ = args, kwargs
        return None

    def capture_warning(message, error, extra=None):
        observed.append(
            {
                "message": str(message),
                "error": str(error),
                "extra": dict(extra or {}),
            }
        )

    monkeypatch.setattr(
        payment_module,
        "_notify_successful_purchase",
        failing_user_notify,
        raising=False,
    )
    monkeypatch.setattr(payment_module, "on_payment", noop_admin_notify, raising=False)
    monkeypatch.setattr(
        payment_module.logger, "warning", capture_warning, raising=False
    )

    await _replay_payment_notifications_if_needed(
        user=user,
        payment_id="replay-user-log-9301",
        days=30,
        amount_external=1000.0,
        amount_from_balance=0.0,
        device_count=1,
        months=1,
        is_auto_payment=False,
        discount_percent=None,
        old_expired_at=user.expired_at,
        new_expired_at=user.expired_at,
        lte_gb_total=0,
        method="yookassa_fallback",
    )

    assert observed
    assert any(
        entry["extra"].get("payment_id") == "replay-user-log-9301"
        and entry["extra"].get("user_id") == user.id
        and entry["extra"].get("effect") == "user"
        for entry in observed
    )


@pytest.mark.asyncio
async def test_fallback_refreshes_lease_before_usage_fetch_and_blocks_reclaim(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import (
        PAYMENT_PROCESSING_STALE_SECONDS,
        _apply_succeeded_payment_fallback,
        _claim_payment_effect_once,
    )
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9201,
        username="u9201",
        full_name="User 9201",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111111111",
    )
    tariff = await Tariffs.create(
        id=9201,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    payment_id = "fallback-lease-fetch-9201"
    yk_payment = types.SimpleNamespace(
        id=payment_id,
        amount=types.SimpleNamespace(value="1000.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 1,
        "tariff_id": tariff.id,
        "device_count": 1,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    async def fake_get_or_none(*args, **kwargs):
        stale_ts = datetime.now(timezone.utc) - timedelta(
            seconds=PAYMENT_PROCESSING_STALE_SECONDS + 5
        )
        await ProcessedPayments.filter(payment_id=payment_id).update(
            last_attempt_at=stale_ts
        )
        tariff_id = kwargs.get("id")
        return await Tariffs.filter(id=tariff_id).first()

    reclaim_observed: dict[str, bool | None] = {"result": None}

    async def fake_fetch_today_lte_usage_gb(_uuid: str):
        reclaim_observed["result"] = await _claim_payment_effect_once(
            payment_id=payment_id,
            user_id=user.id + 100,
            source="webhook",
        )
        return 0.0

    monkeypatch.setattr(
        payment_module.Tariffs, "get_or_none", fake_get_or_none, raising=False
    )
    monkeypatch.setattr(
        payment_module,
        "_fetch_today_lte_usage_gb",
        fake_fetch_today_lte_usage_gb,
        raising=False,
    )

    applied = await _apply_succeeded_payment_fallback(yk_payment, user, metadata)

    assert applied is True
    assert reclaim_observed["result"] is False


@pytest.mark.asyncio
async def test_fallback_external_timeout_prevents_stale_reclaim_duplication(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import (
        _apply_succeeded_payment_fallback,
        _claim_payment_effect_once,
    )
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9202,
        username="u9202",
        full_name="User 9202",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111111112",
    )
    tariff = await Tariffs.create(
        id=9202,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    payment_id = "fallback-timeout-lease-9202"
    yk_payment = types.SimpleNamespace(
        id=payment_id,
        amount=types.SimpleNamespace(value="1000.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 1,
        "tariff_id": tariff.id,
        "device_count": 1,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    monkeypatch.setattr(
        payment_module, "PAYMENT_PROCESSING_STALE_SECONDS", 0.15, raising=False
    )
    monkeypatch.setattr(
        payment_module, "PAYMENT_EXTERNAL_CALL_TIMEOUT_SECONDS", 0.05, raising=False
    )

    fetch_started = asyncio.Event()

    async def fake_hanging_fetch(_uuid: str):
        fetch_started.set()
        await asyncio.sleep(0.30)
        return 0.0

    class _FakeUsersApi:
        async def update_user(self, **_kwargs):
            return None

    class _FakeRemnaWaveClient:
        def __init__(self, *_args, **_kwargs):
            self.users = _FakeUsersApi()

        async def close(self):
            return None

    monkeypatch.setattr(
        payment_module,
        "_fetch_today_lte_usage_gb",
        fake_hanging_fetch,
        raising=False,
    )
    monkeypatch.setattr(
        payment_module, "RemnaWaveClient", _FakeRemnaWaveClient, raising=False
    )

    fallback_task = asyncio.create_task(
        _apply_succeeded_payment_fallback(yk_payment, user, metadata)
    )

    await fetch_started.wait()
    await asyncio.sleep(0.20)
    reclaimed = await _claim_payment_effect_once(
        payment_id=payment_id,
        user_id=user.id + 100,
        source="webhook",
    )
    applied = await fallback_task

    assert applied is True
    assert reclaimed is False

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.effect_applied is True
    assert row.processing_state == "applied"


@pytest.mark.asyncio
async def test_fallback_claim_false_skips_notifications_during_fresh_processing(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9204, username="u9204", full_name="User 9204", is_registered=True
    )
    tariff = await Tariffs.create(
        id=9204,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    payment_id = "fallback-fresh-processing-no-replay-9204"
    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=1000,
        amount_external=1000,
        amount_from_balance=0,
        status="pending",
        processing_state="processing",
        effect_applied=False,
        last_attempt_at=datetime.now(timezone.utc),
    )

    yk_payment = types.SimpleNamespace(
        id=payment_id,
        amount=types.SimpleNamespace(value="1000.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 1,
        "tariff_id": tariff.id,
        "device_count": 1,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    sent = {"user": 0, "admin": 0}

    async def fake_user_notify(*args, **kwargs):
        sent["user"] += 1
        return None

    async def fake_admin_notify(*args, **kwargs):
        sent["admin"] += 1
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", fake_user_notify, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", fake_admin_notify, raising=False)

    applied = await _apply_succeeded_payment_fallback(yk_payment, user, metadata)
    assert applied is True
    assert sent == {"user": 0, "admin": 0}

    marks = await NotificationMarks.filter(user_id=user.id, type="payment_notify").all()
    assert marks == []

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.status == "pending"
    assert row.processing_state == "processing"


@pytest.mark.asyncio
async def test_fallback_claim_false_with_malformed_month_does_not_mark_failed():
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback

    user = await Users.create(
        id=9206, username="u9206", full_name="User 9206", is_registered=True
    )
    payment_id = "fallback-claim-false-bad-month-9206"

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=1000,
        amount_external=1000,
        amount_from_balance=0,
        status="pending",
        processing_state="processing",
        effect_applied=False,
        last_attempt_at=datetime.now(timezone.utc),
    )

    yk_payment = types.SimpleNamespace(
        id=payment_id,
        amount=types.SimpleNamespace(value="1000.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": "bad-month",
        "device_count": 1,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    applied = await _apply_succeeded_payment_fallback(yk_payment, user, metadata)
    assert applied is True

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.processing_state == "processing"
    assert str(row.last_error or "") == ""


@pytest.mark.asyncio
async def test_webhook_claim_false_replays_notifications_after_post_core_crash(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import yookassa_webhook
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9205,
        username="u9205",
        full_name="User 9205",
        is_registered=True,
        expired_at=date.today() + timedelta(days=15),
    )
    previous_expired_at = user.expired_at
    payment_id = "webhook-claim-false-replay-9205"

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=1,
        amount_external=1,
        amount_from_balance=0,
        status="pending",
        processing_state="applied",
        effect_applied=True,
        last_attempt_at=datetime.now(timezone.utc),
    )

    sent = {"user": 0, "admin": 0}

    async def fake_user_notify(*args, **kwargs):
        sent["user"] += 1
        return None

    async def fake_admin_notify(*args, **kwargs):
        sent["admin"] += 1
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", fake_user_notify, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", fake_admin_notify, raising=False)

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_SUCCEEDED
            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="1000.00"),
                status="succeeded",
                metadata={
                    "user_id": str(user.id),
                    "month": 1,
                    "device_count": 1,
                    "amount_from_balance": 150,
                    "lte_gb": 0,
                    "is_auto": False,
                },
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    result_first = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )
    result_second = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert result_first == {"status": "ok"}
    assert result_second == {"status": "ok"}
    assert sent == {"user": 1, "admin": 1}

    user_after = await Users.get(id=user.id)
    assert user_after.expired_at == previous_expired_at

    marks = await NotificationMarks.filter(user_id=user.id, type="payment_notify").all()
    assert len(marks) == 2

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.status == "succeeded"
    assert float(row.amount_external) == pytest.approx(1000.0)
    assert float(row.amount_from_balance) == pytest.approx(150.0)
    assert float(row.amount) == pytest.approx(1150.0)


@pytest.mark.asyncio
async def test_webhook_claim_false_replays_family_to_base_migration_once(monkeypatch):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.payment import yookassa_webhook

    today = date.today()
    user = await Users.create(
        id=9251,
        username="u9251",
        full_name="User 9251",
        is_registered=True,
        expired_at=today + timedelta(days=60),
        hwid_limit=10,
    )
    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=21,
        base_expires_at_snapshot=today + timedelta(days=21),
        family_expires_at=today + timedelta(days=60),
        base_tariff_name="base_1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
    )

    payment_id = "webhook-family-to-base-replay-9251"
    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=1,
        amount_external=1,
        amount_from_balance=0,
        status="pending",
        processing_state="applied",
        effect_applied=True,
        last_attempt_at=datetime.now(timezone.utc),
    )

    sent: dict[str, list[str | None]] = {"user": [], "admin": []}

    async def fake_user_notify(*args, **kwargs):
        sent["user"].append(kwargs.get("migration_direction"))
        return None

    async def fake_admin_notify(*args, **kwargs):
        sent["admin"].append(kwargs.get("migration_direction"))
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", fake_user_notify, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", fake_admin_notify, raising=False)

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_SUCCEEDED
            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="1000.00"),
                status="succeeded",
                metadata={
                    "user_id": str(user.id),
                    "month": 1,
                    "device_count": 3,
                    "amount_from_balance": 150,
                    "lte_gb": 0,
                    "is_auto": False,
                    "tariff_kind": "base",
                },
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    result_first = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )
    result_second = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert result_first == {"status": "ok"}
    assert result_second == {"status": "ok"}
    assert sent == {"user": ["family_to_base"], "admin": ["family_to_base"]}

    marks = await NotificationMarks.filter(user_id=user.id, type="payment_notify").all()
    assert len(marks) == 2


@pytest.mark.asyncio
async def test_manual_payment_canceled_notifications_are_deduped(monkeypatch):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.payment import yookassa_webhook

    user = await Users.create(
        id=9252,
        username="u9252",
        full_name="User 9252",
        is_registered=True,
    )
    payment_id = "webhook-manual-canceled-9252"

    sent = {"user": 0, "admin": 0}

    async def fake_user_canceled(*args, **kwargs):
        _ = args, kwargs
        sent["user"] += 1
        return None

    async def fake_admin_canceled(*args, **kwargs):
        _ = args, kwargs
        sent["admin"] += 1
        return None

    monkeypatch.setattr(
        payment_module,
        "notify_payment_canceled_yookassa",
        fake_user_canceled,
        raising=False,
    )
    monkeypatch.setattr(
        payment_module,
        "notify_manual_payment_canceled",
        fake_admin_canceled,
        raising=False,
    )

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_CANCELED
            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="990.00"),
                status="canceled",
                metadata={
                    "user_id": str(user.id),
                    "month": 1,
                    "device_count": 3,
                    "amount_from_balance": 0,
                    "lte_gb": 0,
                    "is_auto": False,
                },
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    first = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )
    second = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert first == {"status": "ok"}
    assert second == {"status": "ok"}
    assert sent == {"user": 1, "admin": 1}

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.status == "canceled"


@pytest.mark.asyncio
async def test_webhook_claim_false_replay_with_malformed_device_count_still_ok(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import yookassa_webhook
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9207,
        username="u9207",
        full_name="User 9207",
        is_registered=True,
        expired_at=date.today() + timedelta(days=10),
    )
    payment_id = "webhook-claim-false-bad-device-count-9207"

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=1000,
        amount_external=1000,
        amount_from_balance=0,
        status="succeeded",
        processing_state="applied",
        effect_applied=True,
        last_attempt_at=datetime.now(timezone.utc),
    )

    sent = {"user": 0, "admin": 0}

    async def fake_user_notify(*args, **kwargs):
        sent["user"] += 1
        return None

    async def fake_admin_notify(*args, **kwargs):
        sent["admin"] += 1
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", fake_user_notify, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", fake_admin_notify, raising=False)

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_SUCCEEDED
            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="1000.00"),
                status="succeeded",
                metadata={
                    "user_id": str(user.id),
                    "month": 1,
                    "device_count": "oops",
                    "amount_from_balance": 0,
                    "lte_gb": 0,
                    "is_auto": False,
                },
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    result = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert result == {"status": "ok"}
    assert sent == {"user": 1, "admin": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "month_metadata",
    [
        "bad-month",
        None,
    ],
)
async def test_webhook_claim_false_with_invalid_month_still_repairs_financials_without_replay(
    monkeypatch,
    month_metadata,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import yookassa_webhook
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9208,
        username="u9208",
        full_name="User 9208",
        is_registered=True,
        expired_at=date.today() + timedelta(days=10),
    )
    payment_id = f"webhook-claim-false-bad-month-9208-{str(month_metadata)}"

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=1,
        amount_external=1,
        amount_from_balance=0,
        status="pending",
        processing_state="applied",
        effect_applied=True,
        last_attempt_at=datetime.now(timezone.utc),
    )

    sent = {"user": 0, "admin": 0}

    async def fake_user_notify(*args, **kwargs):
        _ = args, kwargs
        sent["user"] += 1
        return None

    async def fake_admin_notify(*args, **kwargs):
        _ = args, kwargs
        sent["admin"] += 1
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", fake_user_notify, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", fake_admin_notify, raising=False)

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_SUCCEEDED
            metadata = {
                "user_id": str(user.id),
                "device_count": 1,
                "amount_from_balance": 120,
                "lte_gb": 0,
                "is_auto": False,
            }
            if month_metadata is not None:
                metadata["month"] = month_metadata

            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="1000.00"),
                status="succeeded",
                metadata=metadata,
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    result = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert result == {"status": "ok"}
    assert sent == {"user": 0, "admin": 0}

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.status == "succeeded"
    assert float(row.amount_external) == pytest.approx(1000.0)
    assert float(row.amount_from_balance) == pytest.approx(120.0)
    assert float(row.amount) == pytest.approx(1120.0)


@pytest.mark.asyncio
async def test_webhook_auto_base_overlay_preserves_family_lte_state(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import yookassa_webhook
    from bloobcat.routes import payment as payment_module

    today = date.today()
    user = await Users.create(
        id=9211,
        username="u9211",
        full_name="User 9211",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111119211",
        expired_at=today + timedelta(days=40),
        hwid_limit=10,
        lte_gb_total=120,
    )
    family_active = await ActiveTariffs.create(
        user=user,
        name="family-12m-active",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=120,
        lte_gb_used=19.5,
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
        family_expires_at=today + timedelta(days=40),
        base_tariff_name="base-1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    lte_status_calls = {"count": 0}

    async def _fake_set_lte_status(*args, **kwargs):
        _ = args, kwargs
        lte_status_calls["count"] += 1
        return None

    async def _noop(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(
        payment_module, "set_lte_squad_status", _fake_set_lte_status, raising=False
    )
    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", _noop, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", _noop, raising=False)
    monkeypatch.setattr(payment_module, "_award_partner_cashback", _noop, raising=False)

    payment_id = "webhook-auto-base-overlay-9211"

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_SUCCEEDED
            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="1000.00"),
                status="succeeded",
                payment_method=None,
                metadata={
                    "user_id": str(user.id),
                    "month": 1,
                    "device_count": 3,
                    "tariff_kind": "base",
                    "amount_from_balance": 0,
                    "lte_gb": 0,
                    "is_auto": True,
                },
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    result = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert result == {"status": "ok"}

    user_after = await Users.get(id=user.id)
    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    family_active_after = await ActiveTariffs.get(id=family_active.id)

    assert int(freeze_after.base_remaining_days or 0) > 20
    assert str(user_after.active_tariff_id) == str(family_active.id)
    assert int(user_after.hwid_limit or 0) == 10
    assert float(family_active_after.lte_gb_used or 0.0) == pytest.approx(19.5)
    assert lte_status_calls["count"] == 0


@pytest.mark.asyncio
async def test_webhook_auto_base_overlay_refreshes_frozen_base_snapshot(monkeypatch):
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import yookassa_webhook
    from bloobcat.routes import payment as payment_module

    today = date.today()
    family_expired_at = today + timedelta(days=40)

    user = await Users.create(
        id=9213,
        username="u9213",
        full_name="User 9213",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111119213",
        expired_at=family_expired_at,
        hwid_limit=10,
        lte_gb_total=120,
    )

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=5,
        base_expires_at_snapshot=today + timedelta(days=5),
        family_expires_at=family_expired_at,
        base_tariff_name="trial",
        base_tariff_months=1,
        base_tariff_price=0,
        base_hwid_limit=1,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.0,
        base_residual_day_fraction=0.0,
    )

    base_tariff = await Tariffs.create(
        id=9213,
        name="base-1m-3d",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=2,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    async def _noop(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", _noop, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", _noop, raising=False)
    monkeypatch.setattr(payment_module, "_award_partner_cashback", _noop, raising=False)

    payment_id = "webhook-auto-base-overlay-snapshot-9213"

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_SUCCEEDED
            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="1000.00"),
                status="succeeded",
                payment_method=None,
                metadata={
                    "user_id": str(user.id),
                    "month": 1,
                    "device_count": 3,
                    "tariff_id": base_tariff.id,
                    "tariff_kind": "base",
                    "amount_from_balance": 0,
                    "lte_gb": 0,
                    "is_auto": True,
                },
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    result = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert result == {"status": "ok"}

    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    expected_price = int(base_tariff.calculate_price(3))

    assert int(freeze_after.base_remaining_days or 0) > 5
    assert freeze_after.base_tariff_name == "base-1m-3d"
    assert int(freeze_after.base_tariff_months or 0) == 1
    assert int(freeze_after.base_tariff_price or 0) == expected_price
    assert int(freeze_after.base_hwid_limit or 0) == 3
    assert float(freeze_after.base_progressive_multiplier or 0.0) == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_webhook_remnawave_retry_uses_remaining_budget_timeout(monkeypatch):
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import yookassa_webhook
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9212,
        username="u9212",
        full_name="User 9212",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111119212",
    )

    async def _noop(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", _noop, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", _noop, raising=False)
    monkeypatch.setattr(payment_module, "_award_partner_cashback", _noop, raising=False)

    class _FakeUsersApi:
        async def update_user(self, **_kwargs):
            return None

    class _FakeRemnaWaveClient:
        def __init__(self, *_args, **_kwargs):
            self.users = _FakeUsersApi()

        async def close(self):
            return None

    monkeypatch.setattr(
        payment_module, "RemnaWaveClient", _FakeRemnaWaveClient, raising=False
    )

    timeout_calls: list[float] = []

    async def _timed_out_external_call(
        awaitable, *, operation: str, timeout: float | None = None
    ):
        if hasattr(awaitable, "close"):
            awaitable.close()
        if operation in {
            "webhook_remnawave_update_user",
            "webhook_remnawave_update_user_recreated",
        }:
            timeout_calls.append(float(timeout or 0.0))
            raise asyncio.TimeoutError("simulated remnawave timeout")
        return None

    monkeypatch.setattr(
        payment_module,
        "_await_payment_external_call",
        _timed_out_external_call,
        raising=False,
    )

    async def _fast_sleep(_seconds: float):
        return None

    monkeypatch.setattr(payment_module.asyncio, "sleep", _fast_sleep, raising=False)

    real_datetime = datetime

    class _FakeDateTime:
        _tick = 0

        @classmethod
        def now(cls, tz=None):
            cls._tick += 1
            value = real_datetime(2026, 1, 1, 0, 0, 0) + timedelta(
                seconds=10 * cls._tick
            )
            if tz is None:
                return value
            return value.replace(tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(payment_module, "datetime", _FakeDateTime, raising=False)

    payment_id = "webhook-remna-budget-timeout-9212"

    class _WebhookNotification:
        def __init__(self, body, headers):
            _ = body, headers
            self.event = payment_module.WebhookNotificationEventType.PAYMENT_SUCCEEDED
            self.object = types.SimpleNamespace(
                id=payment_id,
                amount=types.SimpleNamespace(value="1000.00"),
                status="succeeded",
                payment_method=None,
                metadata={
                    "user_id": str(user.id),
                    "month": 1,
                    "device_count": 1,
                    "tariff_kind": "base",
                    "amount_from_balance": 0,
                    "lte_gb": 0,
                    "is_auto": False,
                },
            )

    class _FakeRequest:
        headers = {}

        async def json(self):
            return {}

    monkeypatch.setattr(
        payment_module, "WebhookNotification", _WebhookNotification, raising=False
    )

    result = await yookassa_webhook(
        request=cast(Any, _FakeRequest()),
        secret=payment_module.yookassa_settings.webhook_secret,
    )

    assert result == {"status": "ok"}
    assert len(timeout_calls) >= 1
    assert all(0 < value <= 60 for value in timeout_calls)
    assert all(
        value < payment_module.PAYMENT_EXTERNAL_CALL_TIMEOUT_SECONDS
        for value in timeout_calls
    )
    assert all(prev >= cur for prev, cur in zip(timeout_calls, timeout_calls[1:]))


@pytest.mark.asyncio
async def test_fallback_retry_after_post_mutation_crash_stays_idempotent(monkeypatch):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9203,
        username="u9203",
        full_name="User 9203",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111111113",
    )
    tariff = await Tariffs.create(
        id=9203,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    payment_id = "fallback-post-mutation-crash-9203"
    yk_payment = types.SimpleNamespace(
        id=payment_id,
        amount=types.SimpleNamespace(value="1000.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 1,
        "tariff_id": tariff.id,
        "device_count": 1,
        "amount_from_balance": 0,
        "lte_gb": 0,
    }

    class _FakeUsersApi:
        async def update_user(self, **_kwargs):
            return None

    class _FakeRemnaWaveClient:
        def __init__(self, *_args, **_kwargs):
            self.users = _FakeUsersApi()

        async def close(self):
            return None

    monkeypatch.setattr(
        payment_module, "RemnaWaveClient", _FakeRemnaWaveClient, raising=False
    )

    sent = {"user": 0, "admin": 0}

    async def fake_user_notify(*args, **kwargs):
        sent["user"] += 1
        return None

    async def fake_admin_notify(*args, **kwargs):
        sent["admin"] += 1
        return None

    monkeypatch.setattr(
        payment_module, "_notify_successful_purchase", fake_user_notify, raising=False
    )
    monkeypatch.setattr(payment_module, "on_payment", fake_admin_notify, raising=False)

    original_refresh = payment_module._refresh_payment_processing_lease
    seen_fallback_sources = 0
    crash_armed = True

    async def crash_after_mutations(*, payment_id: str, user_id: int, source: str):
        nonlocal seen_fallback_sources, crash_armed
        if source == "fallback":
            seen_fallback_sources += 1
            if crash_armed and seen_fallback_sources >= 3:
                crash_armed = False
                raise RuntimeError("simulated crash after core DB mutation")
        await original_refresh(payment_id=payment_id, user_id=user_id, source=source)

    monkeypatch.setattr(
        payment_module,
        "_refresh_payment_processing_lease",
        crash_after_mutations,
        raising=False,
    )

    with pytest.raises(RuntimeError):
        await _apply_succeeded_payment_fallback(yk_payment, user, metadata)

    assert sent == {"user": 0, "admin": 0}

    user_after_crash = await Users.get(id=user.id)
    assert user_after_crash.expired_at is not None
    first_expired_at = user_after_crash.expired_at

    row_after_crash = await ProcessedPayments.get(payment_id=payment_id)
    assert row_after_crash.effect_applied is True
    assert row_after_crash.processing_state == "applied"

    retry = await _apply_succeeded_payment_fallback(
        yk_payment, user_after_crash, metadata
    )
    assert retry is True

    user_after_retry = await Users.get(id=user.id)
    assert user_after_retry.expired_at == first_expired_at

    assert sent == {"user": 1, "admin": 1}

    marks = await NotificationMarks.filter(
        user_id=user.id,
        type="payment_notify",
    ).all()
    assert len(marks) == 2

    retry_again = await _apply_succeeded_payment_fallback(
        yk_payment, user_after_retry, metadata
    )
    assert retry_again is True

    user_after_retry_again = await Users.get(id=user.id)
    assert user_after_retry_again.expired_at == first_expired_at
    assert sent == {"user": 1, "admin": 1}


@pytest.mark.asyncio
async def test_payment_status_masks_foreign_payment_when_metadata_owner_mismatch(
    monkeypatch,
):
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9004, username="u9004", full_name="User 9004", is_registered=True
    )

    fake_payment = types.SimpleNamespace(
        id="foreign-with-meta-owner",
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={"user_id": str(user.id + 1)},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_payment_status("foreign-with-meta-owner", user=user)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_payment_status_succeeded_applies_fallback_and_marks_processed(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9005, username="u9005", full_name="User 9005", is_registered=True
    )
    tariff = await Tariffs.create(
        id=9005,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    fake_payment = types.SimpleNamespace(
        id="succeeded-fallback-9005",
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        payment_method=None,
        metadata={
            "user_id": str(user.id),
            "month": 1,
            "tariff_id": tariff.id,
            "device_count": 1,
            "amount_from_balance": 0,
            "lte_gb": 0,
        },
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    result = await get_payment_status("succeeded-fallback-9005", user=user)

    assert result["payment_id"] == "succeeded-fallback-9005"
    assert result["yookassa_status"] == "succeeded"
    assert result["is_paid"] is True
    assert result["processed"] is True
    assert result["processed_status"] == "succeeded"
    assert result["entitlements_ready"] is True

    processed = await ProcessedPayments.get(payment_id="succeeded-fallback-9005")
    assert processed.effect_applied is True
    assert processed.processing_state == "applied"

    user_after = await Users.get(id=user.id)
    assert user_after.expired_at is not None
    assert user_after.expired_at >= date.today()


@pytest.mark.asyncio
async def test_payment_status_fast_fallback_skips_lte_usage_prefetch(monkeypatch):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9006,
        username="u9006",
        full_name="User 9006",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111119006",
    )
    tariff = await Tariffs.create(
        id=9006,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    fake_payment = types.SimpleNamespace(
        id="succeeded-fast-skip-lte-9006",
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        payment_method=None,
        metadata={
            "user_id": str(user.id),
            "month": 1,
            "tariff_id": tariff.id,
            "device_count": 1,
            "amount_from_balance": 0,
            "lte_gb": 0,
        },
    )

    usage_calls = {"count": 0}

    async def fake_fetch_today_lte_usage_gb(_uuid: str):
        usage_calls["count"] += 1
        return 0.0

    class _FakeUsersApi:
        async def update_user(self, **_kwargs):
            return None

    class _FakeRemnaWaveClient:
        def __init__(self, *_args, **_kwargs):
            self.users = _FakeUsersApi()

        async def close(self):
            return None

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )
    monkeypatch.setattr(
        payment_module,
        "_fetch_today_lte_usage_gb",
        fake_fetch_today_lte_usage_gb,
        raising=False,
    )
    monkeypatch.setattr(
        payment_module, "RemnaWaveClient", _FakeRemnaWaveClient, raising=False
    )

    result = await get_payment_status("succeeded-fast-skip-lte-9006", user=user)

    assert result["processed"] is True
    assert result["processed_status"] == "succeeded"
    assert usage_calls["count"] == 0

    processed = await ProcessedPayments.get(payment_id="succeeded-fast-skip-lte-9006")
    assert processed.effect_applied is True
    assert processed.processing_state == "applied"


@pytest.mark.asyncio
async def test_payment_status_fast_fallback_passes_skip_flag_to_user_save(monkeypatch):
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9014,
        username="u9014",
        full_name="User 9014",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111119014",
    )
    tariff = await Tariffs.create(
        id=9014,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    fake_payment = types.SimpleNamespace(
        id="succeeded-fast-save-flag-9014",
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        payment_method=None,
        metadata={
            "user_id": str(user.id),
            "month": 1,
            "tariff_id": tariff.id,
            "device_count": 1,
            "amount_from_balance": 0,
            "lte_gb": 0,
        },
    )

    save_skip_flags: list[bool] = []
    original_save = Users.save

    async def tracking_save(self, *args, **kwargs):
        save_skip_flags.append(bool(kwargs.get("skip_remnawave_sync", False)))
        return await original_save(self, *args, **kwargs)

    class _FakeUsersApi:
        async def update_user(self, **_kwargs):
            return None

    class _FakeRemnaWaveClient:
        def __init__(self, *_args, **_kwargs):
            self.users = _FakeUsersApi()

        async def close(self):
            return None

    monkeypatch.setattr(Users, "save", tracking_save, raising=False)
    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )
    monkeypatch.setattr(
        payment_module, "RemnaWaveClient", _FakeRemnaWaveClient, raising=False
    )

    result = await get_payment_status("succeeded-fast-save-flag-9014", user=user)

    assert result["processed"] is True
    assert result["processed_status"] == "succeeded"
    assert any(save_skip_flags)


@pytest.mark.asyncio
async def test_payment_status_fast_fallback_uses_bounded_remnawave_timeout_and_applies(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    user = await Users.create(
        id=9007,
        username="u9007",
        full_name="User 9007",
        is_registered=True,
        remnawave_uuid="11111111-1111-1111-1111-111111119007",
    )
    tariff = await Tariffs.create(
        id=9007,
        name="1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )

    fake_payment = types.SimpleNamespace(
        id="succeeded-fast-remna-timeout-9007",
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        payment_method=None,
        metadata={
            "user_id": str(user.id),
            "month": 1,
            "tariff_id": tariff.id,
            "device_count": 1,
            "amount_from_balance": 0,
            "lte_gb": 0,
        },
    )

    remna_state = {
        "started": False,
        "finished": False,
        "cancelled": False,
    }

    class _FakeUsersApi:
        async def update_user(self, **_kwargs):
            remna_state["started"] = True
            try:
                await asyncio.sleep(0.2)
                remna_state["finished"] = True
                return None
            except asyncio.CancelledError:
                remna_state["cancelled"] = True
                raise

    class _FakeRemnaWaveClient:
        def __init__(self, *_args, **_kwargs):
            self.users = _FakeUsersApi()

        async def close(self):
            return None

    async def should_not_fetch_lte(_uuid: str):
        raise AssertionError("fast status fallback must skip LTE usage prefetch")

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )
    monkeypatch.setattr(
        payment_module, "RemnaWaveClient", _FakeRemnaWaveClient, raising=False
    )
    monkeypatch.setattr(
        payment_module,
        "PAYMENT_FAST_STATUS_REMNAWAVE_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )
    monkeypatch.setattr(
        payment_module,
        "_fetch_today_lte_usage_gb",
        should_not_fetch_lte,
        raising=False,
    )

    result = await get_payment_status("succeeded-fast-remna-timeout-9007", user=user)

    assert result["processed"] is True
    assert result["processed_status"] == "succeeded"
    assert remna_state["started"] is True
    assert remna_state["finished"] is False
    assert remna_state["cancelled"] is True

    processed = await ProcessedPayments.get(
        payment_id="succeeded-fast-remna-timeout-9007"
    )
    assert processed.effect_applied is True
    assert processed.processing_state == "applied"


@pytest.mark.asyncio
async def test_upsert_processed_payment_recovers_from_concurrent_create_integrity_error(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.payment import _upsert_processed_payment

    payment_id = "race-upsert-integrity-9301"
    real_create = ProcessedPayments.create
    race_state = {"raised": False}

    async def racing_create(*args, **kwargs):
        if not race_state["raised"]:
            race_state["raised"] = True
            await real_create(
                payment_id=payment_id,
                user_id=9301,
                amount=1,
                amount_external=1,
                amount_from_balance=0,
                status="pending",
                processing_state="pending",
            )
            raise IntegrityError("duplicate key value violates unique constraint")
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(
        payment_module.ProcessedPayments,
        "create",
        racing_create,
        raising=False,
    )

    result = await _upsert_processed_payment(
        payment_id=payment_id,
        user_id=9302,
        amount=1299.0,
        amount_external=1299.0,
        amount_from_balance=0.0,
        status="succeeded",
    )

    assert race_state["raised"] is True
    assert result.payment_id == payment_id

    rows = await ProcessedPayments.filter(payment_id=payment_id)
    assert len(rows) == 1
    assert int(rows[0].user_id) == 9302
    assert str(rows[0].status) == "succeeded"


@pytest.mark.asyncio
async def test_reconcile_canceled_continues_when_single_upsert_fails(monkeypatch):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.tasks import payment_reconcile as reconcile_module

    user = await Users.create(
        id=9303,
        username="u9303",
        full_name="User 9303",
        is_registered=True,
    )

    first = await ProcessedPayments.create(
        payment_id="reconcile-canceled-fail-9303-a",
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="pending",
        processing_state="pending",
    )
    second = await ProcessedPayments.create(
        payment_id="reconcile-canceled-fail-9303-b",
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="pending",
        processing_state="pending",
    )

    now = datetime.now(timezone.utc)
    await ProcessedPayments.filter(id=first.id).update(
        processed_at=now - timedelta(minutes=3)
    )
    await ProcessedPayments.filter(id=second.id).update(
        processed_at=now - timedelta(minutes=2)
    )

    async def fake_fetch_yookassa_payment(payment_id: str):
        return types.SimpleNamespace(
            id=payment_id,
            status="canceled",
            amount=types.SimpleNamespace(value="100.00"),
            metadata={},
        )

    real_upsert = payment_module._upsert_processed_payment
    upsert_calls = {"count": 0}

    async def flaky_upsert(*args, **kwargs):
        upsert_calls["count"] += 1
        if upsert_calls["count"] == 1:
            raise RuntimeError("single upsert failure")
        return await real_upsert(*args, **kwargs)

    monkeypatch.setattr(
        reconcile_module,
        "_fetch_yookassa_payment",
        fake_fetch_yookassa_payment,
        raising=False,
    )
    monkeypatch.setattr(
        payment_module,
        "_upsert_processed_payment",
        flaky_upsert,
        raising=False,
    )

    await reconcile_module.reconcile_pending_payments(batch_limit=10)

    first_after = await ProcessedPayments.get(payment_id=first.payment_id)
    second_after = await ProcessedPayments.get(payment_id=second.payment_id)

    assert upsert_calls["count"] == 2
    assert first_after.status == "pending"
    assert second_after.status == "canceled"

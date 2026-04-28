from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
from datetime import date, datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from tortoise import Tortoise

try:
    from tests._payment_test_stubs import install_stubs
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _payment_test_stubs import install_stubs
    from _sqlite_datetime_compat import register_sqlite_datetime_compat


_MODULE_MISSING = object()


def _restore_modules(saved_modules: dict[str, object]) -> None:
    for name, previous in saved_modules.items():
        if previous is _MODULE_MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _install_module(
    saved_modules: dict[str, object],
    name: str,
    *,
    attrs: dict[str, object] | None = None,
    is_package: bool = False,
) -> types.ModuleType:
    saved_modules[name] = sys.modules.get(name, _MODULE_MISSING)
    module = types.ModuleType(name)
    if is_package:
        module.__path__ = []
    for attr_name, attr_value in (attrs or {}).items():
        setattr(module, attr_name, attr_value)
    sys.modules[name] = module
    return module


def _reload_module(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def _load_real_scheduler():
    scheduler_stub = sys.modules["bloobcat.scheduler"]
    sys.modules.pop("bloobcat.scheduler", None)
    return importlib.import_module("bloobcat.scheduler"), scheduler_stub


def _restore_scheduler_stub(scheduler_stub: types.ModuleType) -> None:
    sys.modules.pop("bloobcat.scheduler", None)
    sys.modules["bloobcat.scheduler"] = scheduler_stub


async def _noop_async(*args, **kwargs):
    _ = args, kwargs
    return None


async def _truthy_async(*args, **kwargs):
    _ = args, kwargs
    return True


def _enable_yookassa_auto_renewal(monkeypatch):
    from bloobcat.settings import payment_settings

    monkeypatch.setattr(payment_settings, "auto_renewal_mode", "yookassa")


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    restore_stubs = install_stubs()
    saved_modules: dict[str, object] = {}

    renewal_module = sys.modules["bloobcat.bot.notifications.subscription.renewal"]
    renewal_module.notify_subscription_cancelled_after_failures = _truthy_async

    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.subscription.expiration",
        attrs={
            "notify_expiring_subscription": _truthy_async,
            "notify_auto_payment": _truthy_async,
        },
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.subscription.key",
        attrs={"on_disabled": _truthy_async},
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.trial.extended",
        attrs={"notify_trial_extended": _truthy_async},
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.trial.end",
        attrs={"notify_trial_ended": _truthy_async},
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.trial.expiring",
        attrs={"notify_expiring_trial": _truthy_async},
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.trial.pre_expiring_3d",
        attrs={"notify_trial_three_days_left": _truthy_async},
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.trial.no_trial",
        attrs={"notify_no_trial_taken": _truthy_async},
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.winback",
        is_package=True,
    )
    _install_module(
        saved_modules,
        "bloobcat.bot.notifications.winback.discount_offer",
        attrs={"notify_winback_discount_offer": _truthy_async},
    )
    _install_module(
        saved_modules,
        "bloobcat.routes.payment",
        attrs={
            "create_auto_payment": _truthy_async,
            "build_auto_payment_preview": _truthy_async,
        },
    )

    for module_name, attrs in {
        "bloobcat.tasks.referral_prompts": {
            "run_referral_prompts_scheduler": _noop_async,
        },
        "bloobcat.tasks.remnawave_updater": {
            "run_remnawave_scheduler": _noop_async,
        },
        "bloobcat.tasks.trial_active_tariff_fix": {
            "run_trial_active_tariff_fix_scheduler": _noop_async,
        },
        "bloobcat.tasks.lte_usage_limiter": {
            "run_lte_usage_limiter_scheduler": _noop_async,
            "run_lte_usage_limiter_quick_scheduler": _noop_async,
        },
        "bloobcat.tasks.payment_reconcile": {
            "run_payment_reconcile_scheduler": _noop_async,
        },
        "bloobcat.tasks.remnawave_delete_retry": {
            "run_remnawave_delete_retry_scheduler": _noop_async,
        },
        "bloobcat.tasks.subscription_resume": {
            "run_subscription_resume_scheduler": _noop_async,
        },
    }.items():
        _install_module(saved_modules, module_name, attrs=attrs)

    try:
        yield
    finally:
        _restore_modules(saved_modules)
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
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.discounts",
                        "bloobcat.db.notifications",
                        "bloobcat.db.payments",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )

    from bloobcat.db.users import Users
    from tortoise.backends.sqlite.schema_generator import SqliteSchemaGenerator

    Users._meta.fk_fields.discard("active_tariff")
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    if users_active_tariff_fk is not None:
        users_active_tariff_fk.reference = False
        users_active_tariff_fk.db_constraint = False

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
        [table["table_creation_string"] for table in tables]
        + [m2m for table in tables for m2m in table["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.parametrize(
    ("raw_dt", "expected_dt"),
    [
        (
            datetime(2026, 4, 6, 23, 59, tzinfo=timezone(timedelta(hours=3))),
            datetime(2026, 4, 6, 23, 59, tzinfo=timezone(timedelta(hours=3))),
        ),
        (
            datetime(2026, 4, 7, 0, 0, tzinfo=timezone(timedelta(hours=3))),
            datetime(2026, 4, 7, 8, 0, tzinfo=timezone(timedelta(hours=3))),
        ),
        (
            datetime(2026, 4, 7, 1, 30, tzinfo=timezone(timedelta(hours=3))),
            datetime(2026, 4, 7, 8, 0, tzinfo=timezone(timedelta(hours=3))),
        ),
        (
            datetime(2026, 4, 7, 7, 59, tzinfo=timezone(timedelta(hours=3))),
            datetime(2026, 4, 7, 8, 0, tzinfo=timezone(timedelta(hours=3))),
        ),
        (
            datetime(2026, 4, 7, 8, 0, tzinfo=timezone(timedelta(hours=3))),
            datetime(2026, 4, 7, 8, 0, tzinfo=timezone(timedelta(hours=3))),
        ),
        (
            datetime(2026, 4, 7, 9, 15, tzinfo=timezone(timedelta(hours=3))),
            datetime(2026, 4, 7, 9, 15, tzinfo=timezone(timedelta(hours=3))),
        ),
    ],
)
def test_normalize_user_notification_eta_respects_moscow_quiet_hours(
    raw_dt: datetime,
    expected_dt: datetime,
):
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    normalized = quiet_hours.normalize_user_notification_eta(raw_dt)

    assert normalized == expected_dt


@pytest.mark.asyncio
async def test_schedule_user_tasks_moves_subscription_user_notifications_to_morning(
    monkeypatch,
):
    from bloobcat.db.users import Users

    user = await Users.create(
        id=4101,
        username="sub4101",
        full_name="Subscription 4101",
        is_registered=True,
        is_subscribed=True,
        expired_at=date(2026, 4, 10),
    )

    real_scheduler, scheduler_stub = _load_real_scheduler()
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")
    captured: list[tuple[str, datetime, tuple[object, ...], bool]] = []

    async def _dummy_task():
        return None

    def fake_schedule_coro(at_time, coro, *args, skip_if_past=False):
        captured.append((coro.__name__, at_time, args, skip_if_past))
        return asyncio.create_task(_dummy_task())

    try:
        monkeypatch.setattr(real_scheduler, "schedule_coro", fake_schedule_coro)
        await real_scheduler.schedule_user_tasks(user)
    finally:
        _restore_scheduler_stub(scheduler_stub)

    expiring = {
        int(args[2]): at_time
        for name, at_time, args, _ in captured
        if name == "_exec_notify_expiring"
    }
    expired_eta = next(
        at_time for name, at_time, _, _ in captured if name == "_exec_notify_expired"
    )

    assert expiring[3] == datetime(
        2026, 4, 7, 8, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert expiring[2] == datetime(
        2026, 4, 8, 8, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert expiring[1] == datetime(
        2026, 4, 9, 12, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert expired_eta == datetime(
        2026, 4, 10, 8, 0, tzinfo=quiet_hours.MOSCOW
    )


@pytest.mark.asyncio
async def test_schedule_user_tasks_keeps_trial_end_at_midnight_and_marketing_at_morning(
    monkeypatch,
):
    from bloobcat.db.users import Users

    user = await Users.create(
        id=4102,
        username="trial4102",
        full_name="Trial 4102",
        is_registered=True,
        is_trial=True,
        expired_at=date(2026, 4, 10),
    )

    real_scheduler, scheduler_stub = _load_real_scheduler()
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")
    captured: list[tuple[str, datetime, tuple[object, ...], bool]] = []

    async def _dummy_task():
        return None

    def fake_schedule_coro(at_time, coro, *args, skip_if_past=False):
        captured.append((coro.__name__, at_time, args, skip_if_past))
        return asyncio.create_task(_dummy_task())

    try:
        monkeypatch.setattr(real_scheduler, "schedule_coro", fake_schedule_coro)
        await real_scheduler.schedule_user_tasks(user)
    finally:
        _restore_scheduler_stub(scheduler_stub)

    eta_by_name = {name: at_time for name, at_time, _, _ in captured}

    assert eta_by_name["_exec_notify_trial_1_day_left"] == datetime(
        2026, 4, 9, 8, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert eta_by_name["_exec_notify_expiring_trial"] == datetime(
        2026, 4, 9, 12, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert eta_by_name["_exec_notify_trial_end"] == datetime(
        2026, 4, 10, 0, 0, tzinfo=quiet_hours.MOSCOW
    )


@pytest.mark.asyncio
async def test_exec_notify_trial_end_defers_message_but_updates_state_during_quiet_hours(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    user = await Users.create(
        id=4201,
        username="trial-end-4201",
        full_name="Trial End 4201",
        is_registered=True,
        is_trial=True,
        expired_at=date.today(),
    )

    sent_to: list[int] = []

    async def fake_notify(user_obj):
        sent_to.append(int(user_obj.id))
        return True

    real_scheduler, scheduler_stub = _load_real_scheduler()
    try:
        monkeypatch.setattr(real_scheduler, "notify_trial_ended", fake_notify)
        monkeypatch.setattr(
            real_scheduler,
            "is_quiet_hours",
            lambda *_args, **_kwargs: True,
        )
        await real_scheduler._exec_notify_trial_end(user.id, user.expired_at)
    finally:
        _restore_scheduler_stub(scheduler_stub)

    user_after = await Users.get(id=user.id)
    assert user_after.is_trial is False
    assert sent_to == []
    assert await NotificationMarks.filter(
        user_id=user.id,
        type=quiet_hours.PENDING_TRIAL_ENDED_MARK_TYPE,
        key=str(user.expired_at),
    ).exists()
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type="trial_ended",
        key=str(user.expired_at),
    ).exists()


@pytest.mark.asyncio
async def test_exec_cancel_if_unpaid_defers_message_but_updates_state_during_quiet_hours(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    user = await Users.create(
        id=4202,
        username="cancel-4202",
        full_name="Cancel 4202",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4202",
        expired_at=date.today(),
    )

    sent_to: list[int] = []

    async def fake_notify(user_obj):
        sent_to.append(int(user_obj.id))
        return True

    real_scheduler, scheduler_stub = _load_real_scheduler()
    try:
        _enable_yookassa_auto_renewal(monkeypatch)
        monkeypatch.setattr(
            real_scheduler,
            "notify_subscription_cancelled_after_failures",
            fake_notify,
        )
        monkeypatch.setattr(
            real_scheduler,
            "is_quiet_hours",
            lambda *_args, **_kwargs: True,
        )
        await real_scheduler._exec_cancel_if_unpaid(user.id, user.expired_at)
    finally:
        _restore_scheduler_stub(scheduler_stub)

    user_after = await Users.get(id=user.id)
    assert user_after.is_subscribed is False
    assert user_after.renew_id is None
    assert sent_to == []
    assert await NotificationMarks.filter(
        user_id=user.id,
        type=quiet_hours.PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
        key=str(user.expired_at),
    ).exists()
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type=quiet_hours.SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
        key=str(user.expired_at),
    ).exists()


@pytest.mark.asyncio
async def test_exec_notify_expiring_skips_mark_when_delivery_not_confirmed(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    user = await Users.create(
        id=4203,
        username="sub-4203",
        full_name="Subscription 4203",
        is_registered=True,
        is_subscribed=True,
        expired_at=date(2026, 4, 10),
    )

    real_scheduler, scheduler_stub = _load_real_scheduler()
    try:
        monkeypatch.setattr(
            real_scheduler,
            "notify_expiring_subscription",
            lambda _user: asyncio.sleep(0, result=False),
        )
        await real_scheduler._exec_notify_expiring(user.id, user.expired_at, 3)
    finally:
        _restore_scheduler_stub(scheduler_stub)

    assert not await NotificationMarks.filter(
        user_id=user.id,
        type="subscription_expiring",
        key="3d:2026-04-10",
    ).exists()


@pytest.mark.asyncio
async def test_exec_cancel_if_unpaid_requeues_when_delivery_not_confirmed(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    user = await Users.create(
        id=4204,
        username="cancel-4204",
        full_name="Cancel 4204",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4204",
        expired_at=date.today(),
    )

    real_scheduler, scheduler_stub = _load_real_scheduler()
    try:
        _enable_yookassa_auto_renewal(monkeypatch)
        monkeypatch.setattr(
            real_scheduler,
            "notify_subscription_cancelled_after_failures",
            lambda _user: asyncio.sleep(0, result=False),
        )
        monkeypatch.setattr(
            real_scheduler,
            "is_quiet_hours",
            lambda *_args, **_kwargs: False,
        )
        await real_scheduler._exec_cancel_if_unpaid(user.id, user.expired_at)
    finally:
        _restore_scheduler_stub(scheduler_stub)

    user_after = await Users.get(id=user.id)
    assert user_after.is_subscribed is False
    assert user_after.renew_id is None
    assert await NotificationMarks.filter(
        user_id=user.id,
        type=quiet_hours.PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
        key=str(user.expired_at),
    ).exists()
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type=quiet_hours.SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
        key=str(user.expired_at),
    ).exists()


@pytest.mark.asyncio
async def test_cleanup_missed_cancellations_requeues_when_delivery_not_confirmed(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.cleanup_missed_cancellations")
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    user = await Users.create(
        id=4205,
        username="cleanup-4205",
        full_name="Cleanup 4205",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4205",
        expired_at=date.today() - timedelta(days=1),
    )

    async def fake_notify(_user_obj):
        return False

    monkeypatch.setattr(module, "notify_subscription_cancelled_after_failures", fake_notify)
    monkeypatch.setattr(module, "is_quiet_hours", lambda *_args, **_kwargs: False)
    _enable_yookassa_auto_renewal(monkeypatch)

    cancelled = await module.cleanup_missed_cancellations_once()

    user_after = await Users.get(id=user.id)
    assert cancelled == 1
    assert user_after.is_subscribed is False
    assert user_after.renew_id is None
    assert await NotificationMarks.filter(
        user_id=user.id,
        type=quiet_hours.PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
        key=str(user.expired_at),
    ).exists()
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type=quiet_hours.SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
        key=str(user.expired_at),
    ).exists()


@pytest.mark.asyncio
async def test_retry_trial_notifications_waits_until_morning_and_sends_once(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.retry_trial_notifications")
    user = await Users.create(
        id=4301,
        username="retry4301",
        full_name="Retry Trial 4301",
        is_registered=True,
        is_trial=True,
        expired_at=date.today() + timedelta(days=1),
    )
    user.registration_date = datetime.now(timezone.utc) - timedelta(hours=48)
    await user.save(update_fields=["registration_date"])

    sent: list[int] = []

    async def fake_notify(user_obj, hours):
        _ = user_obj
        sent.append(int(hours))
        return True

    monkeypatch.setattr(module, "notify_no_trial_taken", fake_notify)
    monkeypatch.setattr(module, "is_quiet_hours", lambda *_args, **_kwargs: True)

    processed, notified = await module.retry_send_missed_trial_notifications_once()

    assert (processed, notified) == (0, 0)
    assert sent == []
    assert await NotificationMarks.all().count() == 0

    monkeypatch.setattr(module, "is_quiet_hours", lambda *_args, **_kwargs: False)

    processed, notified = await module.retry_send_missed_trial_notifications_once()

    assert processed == 1
    assert notified == 2
    assert sent == [2, 24]

    processed_again, notified_again = await module.retry_send_missed_trial_notifications_once()

    assert processed_again == 1
    assert notified_again == 0


@pytest.mark.asyncio
async def test_retry_trial_extension_notifications_waits_until_morning_and_sends_once(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.retry_trial_extension_notifications")
    user = await Users.create(
        id=4302,
        username="ext4302",
        full_name="Extension Retry 4302",
        is_registered=True,
        is_trial=True,
        expired_at=date.today() + timedelta(days=1),
    )
    await NotificationMarks.create(
        user_id=user.id,
        type="trial_extension_applied",
        key=str(user.expired_at),
    )

    sent_to: list[int] = []

    async def fake_notify(user_obj, extension_days):
        _ = extension_days
        sent_to.append(int(user_obj.id))
        return True

    monkeypatch.setattr(module, "notify_trial_extended", fake_notify)
    monkeypatch.setattr(module, "is_quiet_hours", lambda *_args, **_kwargs: True)

    processed, notified = await module.retry_trial_extension_notifications_once()

    assert (processed, notified) == (0, 0)
    assert sent_to == []
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type="trial_extension_notified",
        key=str(user.expired_at),
    ).exists()

    monkeypatch.setattr(module, "is_quiet_hours", lambda *_args, **_kwargs: False)

    processed, notified = await module.retry_trial_extension_notifications_once()

    assert processed == 1
    assert notified == 1
    assert sent_to == [user.id]
    assert await NotificationMarks.filter(
        user_id=user.id,
        type="trial_extension_notified",
        key=str(user.expired_at),
    ).exists()


@pytest.mark.asyncio
async def test_subscription_expiring_catchup_uses_morning_window_for_3d_and_2d(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.subscription_expiring_catchup")
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    three_days_user = await Users.create(
        id=4401,
        username="sub3d4401",
        full_name="Subscription 3d 4401",
        is_registered=True,
        is_subscribed=True,
        expired_at=date(2026, 4, 10),
    )
    two_days_user = await Users.create(
        id=4402,
        username="sub2d4402",
        full_name="Subscription 2d 4402",
        is_registered=True,
        is_subscribed=True,
        expired_at=date(2026, 4, 9),
    )
    one_day_user = await Users.create(
        id=4403,
        username="sub1d4403",
        full_name="Subscription 1d 4403",
        is_registered=True,
        is_subscribed=True,
        expired_at=date(2026, 4, 8),
    )

    sent_to: list[int] = []

    async def fake_notify(user_obj):
        sent_to.append(int(user_obj.id))
        return True

    monkeypatch.setattr(module, "notify_expiring_subscription", fake_notify)

    midnight_total = await module.subscription_expiring_catchup_once(
        datetime(2026, 4, 7, 0, 30, tzinfo=quiet_hours.MOSCOW)
    )
    assert midnight_total == 0

    morning_total = await module.subscription_expiring_catchup_once(
        datetime.combine(
            date(2026, 4, 7),
            time(8, 30),
            tzinfo=quiet_hours.MOSCOW,
        )
    )
    assert morning_total == 2
    assert sent_to == [three_days_user.id, two_days_user.id]
    assert await NotificationMarks.filter(
        user_id=three_days_user.id,
        type="subscription_expiring",
        key="3d:2026-04-10",
    ).exists()
    assert await NotificationMarks.filter(
        user_id=two_days_user.id,
        type="subscription_expiring",
        key="2d:2026-04-09",
    ).exists()

    noon_total = await module.subscription_expiring_catchup_once(
        datetime.combine(
            date(2026, 4, 7),
            time(12, 5),
            tzinfo=quiet_hours.MOSCOW,
        )
    )
    assert noon_total == 1
    assert sent_to == [three_days_user.id, two_days_user.id, one_day_user.id]
    assert await NotificationMarks.filter(
        user_id=one_day_user.id,
        type="subscription_expiring",
        key="1d:2026-04-08",
    ).exists()


@pytest.mark.asyncio
async def test_subscription_expiring_catchup_skips_mark_when_delivery_not_confirmed(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.subscription_expiring_catchup")
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    user = await Users.create(
        id=4404,
        username="sub3d4404",
        full_name="Subscription 3d 4404",
        is_registered=True,
        is_subscribed=True,
        expired_at=date(2026, 4, 10),
    )

    async def fake_notify(_user_obj):
        return False

    monkeypatch.setattr(module, "notify_expiring_subscription", fake_notify)

    total = await module.subscription_expiring_catchup_once(
        datetime.combine(
            date(2026, 4, 7),
            time(8, 30),
            tzinfo=quiet_hours.MOSCOW,
        )
    )

    assert total == 0
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type="subscription_expiring",
        key="3d:2026-04-10",
    ).exists()


@pytest.mark.asyncio
async def test_quiet_hours_dispatcher_sends_pending_marks_and_morning_catchups(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.quiet_hours_notifications")
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    today = date(2026, 4, 7)

    trial_user = await Users.create(
        id=4501,
        username="trial4501",
        full_name="Pending Trial 4501",
        is_registered=True,
        is_trial=False,
        expired_at=today,
    )
    cancel_user = await Users.create(
        id=4502,
        username="cancel4502",
        full_name="Pending Cancel 4502",
        is_registered=True,
        is_subscribed=False,
        expired_at=today,
    )
    winback_user = await Users.create(
        id=4503,
        username="win4503",
        full_name="Pending Winback 4503",
        is_registered=True,
        expired_at=today - timedelta(days=7),
    )
    expired_user = await Users.create(
        id=4504,
        username="expired4504",
        full_name="Expired Catchup 4504",
        is_registered=True,
        is_subscribed=True,
        expired_at=today,
    )
    trial_marketing_user = await Users.create(
        id=4505,
        username="marketing4505",
        full_name="Marketing Catchup 4505",
        is_registered=True,
        is_trial=True,
        expired_at=today + timedelta(days=1),
    )

    await NotificationMarks.create(
        user_id=trial_user.id,
        type=quiet_hours.PENDING_TRIAL_ENDED_MARK_TYPE,
        key=str(today),
    )
    await NotificationMarks.create(
        user_id=cancel_user.id,
        type=quiet_hours.PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
        key=str(today),
    )
    await NotificationMarks.create(
        user_id=winback_user.id,
        type=quiet_hours.PENDING_WINBACK_DISCOUNT_MARK_TYPE,
        key=quiet_hours.build_winback_notification_key(today + timedelta(days=7)),
        meta=json.dumps(
            {
                "discount_percent": 25,
                "expires_at": (today + timedelta(days=7)).isoformat(),
            }
        ),
    )

    trial_sent: list[int] = []
    cancel_sent: list[int] = []
    winback_sent: list[int] = []
    expired_sent: list[int] = []
    marketing_sent: list[int] = []

    async def fake_trial_notify(user_obj):
        trial_sent.append(int(user_obj.id))
        return True

    async def fake_cancel_notify(user_obj):
        cancel_sent.append(int(user_obj.id))
        return True

    async def fake_winback_notify(user_obj, percent, expires_at):
        winback_sent.append((int(user_obj.id), int(percent), expires_at))
        return True

    async def fake_on_disabled(user_obj):
        expired_sent.append(int(user_obj.id))
        return True

    async def fake_trial_marketing(user_obj):
        marketing_sent.append(int(user_obj.id))
        return True

    monkeypatch.setattr(module, "notify_trial_ended", fake_trial_notify)
    monkeypatch.setattr(
        module,
        "notify_subscription_cancelled_after_failures",
        fake_cancel_notify,
    )
    monkeypatch.setattr(module, "notify_winback_discount_offer", fake_winback_notify)
    monkeypatch.setattr(module, "on_disabled", fake_on_disabled)
    monkeypatch.setattr(module, "notify_trial_three_days_left", fake_trial_marketing)

    total = await module.quiet_hours_notifications_once(
        datetime.combine(today, time(8, 30), tzinfo=quiet_hours.MOSCOW)
    )

    assert total == 5
    assert trial_sent == [trial_user.id]
    assert cancel_sent == [cancel_user.id]
    assert winback_sent == [(winback_user.id, 25, today + timedelta(days=7))]
    assert expired_sent == [expired_user.id]
    assert marketing_sent == [trial_marketing_user.id]
    assert await NotificationMarks.filter(
        user_id=trial_user.id,
        type="trial_ended",
        key=str(today),
    ).exists()
    assert await NotificationMarks.filter(
        user_id=cancel_user.id,
        type=quiet_hours.SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
        key=str(today),
    ).exists()
    assert await NotificationMarks.filter(
        user_id=winback_user.id,
        type="winback_discount",
        key=quiet_hours.build_winback_notification_key(today + timedelta(days=7)),
    ).exists()
    assert await NotificationMarks.filter(
        user_id=expired_user.id,
        type=quiet_hours.SUBSCRIPTION_EXPIRED_MARK_TYPE,
        key=str(today),
    ).exists()
    assert await NotificationMarks.filter(
        user_id=trial_marketing_user.id,
        type="trial_pre_expiring",
        key=f"1d:{today + timedelta(days=1)}",
    ).exists()
    assert not await NotificationMarks.filter(
        type=quiet_hours.PENDING_TRIAL_ENDED_MARK_TYPE
    ).exists()
    assert not await NotificationMarks.filter(
        type=quiet_hours.PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE
    ).exists()
    assert not await NotificationMarks.filter(
        type=quiet_hours.PENDING_WINBACK_DISCOUNT_MARK_TYPE
    ).exists()


@pytest.mark.asyncio
async def test_quiet_hours_dispatcher_keeps_pending_mark_when_delivery_not_confirmed(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.quiet_hours_notifications")
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    today = date(2026, 4, 7)
    trial_user = await Users.create(
        id=4506,
        username="trial4506",
        full_name="Pending Trial 4506",
        is_registered=True,
        is_trial=False,
        expired_at=today,
    )

    await NotificationMarks.create(
        user_id=trial_user.id,
        type=quiet_hours.PENDING_TRIAL_ENDED_MARK_TYPE,
        key=str(today),
    )

    async def fake_trial_notify(_user_obj):
        return False

    monkeypatch.setattr(module, "notify_trial_ended", fake_trial_notify)

    total = await module.quiet_hours_notifications_once(
        datetime.combine(today, time(8, 30), tzinfo=quiet_hours.MOSCOW)
    )

    assert total == 0
    assert await NotificationMarks.filter(
        user_id=trial_user.id,
        type=quiet_hours.PENDING_TRIAL_ENDED_MARK_TYPE,
        key=str(today),
    ).exists()
    assert not await NotificationMarks.filter(
        user_id=trial_user.id,
        type="trial_ended",
        key=str(today),
    ).exists()


@pytest.mark.asyncio
async def test_quiet_hours_dispatcher_keeps_pending_winback_mark_when_delivery_not_confirmed(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.quiet_hours_notifications")
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    today = date(2026, 4, 7)
    expires_at = today + timedelta(days=7)
    winback_user = await Users.create(
        id=4507,
        username="winback4507",
        full_name="Pending Winback 4507",
        is_registered=True,
        expired_at=today - timedelta(days=30),
    )

    await NotificationMarks.create(
        user_id=winback_user.id,
        type=quiet_hours.PENDING_WINBACK_DISCOUNT_MARK_TYPE,
        key=quiet_hours.build_winback_notification_key(expires_at),
        meta=quiet_hours.build_pending_meta(
            discount_percent=25,
            expires_at=expires_at.isoformat(),
        ),
    )

    async def fake_winback_notify(_user_obj, _percent, _expires_at):
        return False

    monkeypatch.setattr(module, "notify_winback_discount_offer", fake_winback_notify)

    total = await module.quiet_hours_notifications_once(
        datetime.combine(today, time(8, 30), tzinfo=quiet_hours.MOSCOW)
    )

    assert total == 0
    assert await NotificationMarks.filter(
        user_id=winback_user.id,
        type=quiet_hours.PENDING_WINBACK_DISCOUNT_MARK_TYPE,
        key=quiet_hours.build_winback_notification_key(expires_at),
    ).exists()
    assert not await NotificationMarks.filter(
        user_id=winback_user.id,
        type="winback_discount",
        key=quiet_hours.build_winback_notification_key(expires_at),
    ).exists()


@pytest.mark.asyncio
async def test_winback_offer_is_created_at_night_but_user_message_is_deferred(
    monkeypatch,
):
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.winback_discounts")
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")

    user = await Users.create(
        id=4601,
        username="winback4601",
        full_name="Winback 4601",
        is_registered=True,
        expired_at=date.today() - timedelta(days=module.CHURN_DAYS),
    )

    sent_to: list[int] = []

    async def fake_notify(user_obj, discount_percent, expires_at):
        _ = discount_percent, expires_at
        sent_to.append(int(user_obj.id))
        return True

    monkeypatch.setattr(module, "notify_winback_discount_offer", fake_notify)
    monkeypatch.setattr(module, "is_quiet_hours", lambda *_args, **_kwargs: True)

    await module.create_winback_discounts()

    discount = await PersonalDiscount.get(user_id=user.id, source="winback")
    pending_mark = await NotificationMarks.get(
        user_id=user.id,
        type=quiet_hours.PENDING_WINBACK_DISCOUNT_MARK_TYPE,
    )

    assert int(discount.percent) == 25
    assert int(discount.remaining_uses or 0) == 1
    assert sent_to == []
    assert pending_mark.key.startswith("offer:")
    meta = quiet_hours.parse_pending_meta(pending_mark.meta)
    assert int(meta["discount_percent"]) == 25
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type="winback_discount",
    ).exists()


@pytest.mark.asyncio
async def test_schedule_user_tasks_adds_evening_auto_payment_reminders(
    monkeypatch,
):
    from bloobcat.db.users import Users

    user = await Users.create(
        id=4701,
        username="auto4701",
        full_name="Auto Reminder 4701",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4701",
        expired_at=date(2026, 4, 10),
    )

    real_scheduler, scheduler_stub = _load_real_scheduler()
    quiet_hours = _reload_module("bloobcat.tasks.quiet_hours")
    captured: list[tuple[str, datetime, tuple[object, ...], bool]] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(2026, 4, 4, 12, 0, tzinfo=quiet_hours.MOSCOW)
            if tz is None:
                return base.replace(tzinfo=None)
            return base.astimezone(tz)

    async def _dummy_task():
        return None

    def fake_schedule_coro(at_time, coro, *args, skip_if_past=False):
        captured.append((coro.__name__, at_time, args, skip_if_past))
        return asyncio.create_task(_dummy_task())

    try:
        _enable_yookassa_auto_renewal(monkeypatch)
        monkeypatch.setattr(real_scheduler, "datetime", FrozenDateTime)
        monkeypatch.setattr(real_scheduler, "schedule_coro", fake_schedule_coro)
        await real_scheduler.schedule_user_tasks(user)
    finally:
        _restore_scheduler_stub(scheduler_stub)

    reminder_times = {
        int(args[2]): at_time
        for name, at_time, args, _ in captured
        if name == "_exec_notify_auto_payment_reminder"
    }
    auto_payment_times = {
        int(args[2]): at_time
        for name, at_time, args, _ in captured
        if name == "_exec_auto_payment"
    }

    assert reminder_times[4] == datetime(
        2026, 4, 5, 20, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert reminder_times[3] == datetime(
        2026, 4, 6, 20, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert reminder_times[2] == datetime(
        2026, 4, 7, 20, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert auto_payment_times[4] == datetime(
        2026, 4, 6, 0, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert auto_payment_times[3] == datetime(
        2026, 4, 7, 0, 0, tzinfo=quiet_hours.MOSCOW
    )
    assert auto_payment_times[2] == datetime(
        2026, 4, 8, 0, 0, tzinfo=quiet_hours.MOSCOW
    )


@pytest.mark.asyncio
async def test_send_auto_payment_reminder_sends_once_and_creates_mark(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.auto_payment_reminders")
    user = await Users.create(
        id=4702,
        username="auto4702",
        full_name="Auto Reminder 4702",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4702",
        expired_at=date(2026, 4, 10),
    )

    captured: list[dict[str, object]] = []

    async def fake_preview(_user_obj):
        return types.SimpleNamespace(
            total_amount=2190.0,
            amount_external=2190.0,
            amount_from_balance=0.0,
        )

    async def fake_notify(_user_obj, **kwargs):
        captured.append(kwargs)
        return True

    monkeypatch.setattr(module, "build_auto_payment_preview", fake_preview)
    monkeypatch.setattr(module, "notify_auto_payment", fake_notify)
    _enable_yookassa_auto_renewal(monkeypatch)

    sent = await module.send_auto_payment_reminder_if_needed(
        user.id,
        date(2026, 4, 10),
        4,
    )
    sent_again = await module.send_auto_payment_reminder_if_needed(
        user.id,
        date(2026, 4, 10),
        4,
    )

    key = module.build_auto_payment_reminder_key(
        planned_expired=date(2026, 4, 10),
        days_before=4,
    )
    mark = await NotificationMarks.get(
        user_id=user.id,
        type=module.AUTO_PAYMENT_REMINDER_MARK_TYPE,
        key=key,
    )

    assert sent is True
    assert sent_again is False
    assert len(captured) == 1
    assert captured[0]["total_amount"] == 2190.0
    assert captured[0]["amount_external"] == 2190.0
    assert captured[0]["amount_from_balance"] == 0.0
    assert captured[0]["charge_date"] == date(2026, 4, 6)
    assert mark.meta is None


@pytest.mark.asyncio
async def test_send_auto_payment_reminder_releases_mark_when_delivery_not_confirmed(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.auto_payment_reminders")
    user = await Users.create(
        id=4706,
        username="auto4706",
        full_name="Auto Reminder 4706",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4706",
        expired_at=date(2026, 4, 10),
    )

    async def fake_preview(_user_obj):
        return types.SimpleNamespace(
            total_amount=2190.0,
            amount_external=2190.0,
            amount_from_balance=0.0,
        )

    async def fake_notify(_user_obj, **_kwargs):
        return False

    monkeypatch.setattr(module, "build_auto_payment_preview", fake_preview)
    monkeypatch.setattr(module, "notify_auto_payment", fake_notify)
    _enable_yookassa_auto_renewal(monkeypatch)

    sent = await module.send_auto_payment_reminder_if_needed(
        user.id,
        date(2026, 4, 10),
        4,
    )

    key = module.build_auto_payment_reminder_key(
        planned_expired=date(2026, 4, 10),
        days_before=4,
    )
    assert sent is False
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type=module.AUTO_PAYMENT_REMINDER_MARK_TYPE,
        key=key,
    ).exists()


@pytest.mark.asyncio
async def test_send_auto_payment_reminder_skips_when_auto_renewal_disabled(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.auto_payment_reminders")
    user = await Users.create(
        id=4703,
        username="auto4703",
        full_name="Auto Reminder 4703",
        is_registered=True,
        is_subscribed=True,
        renew_id=None,
        expired_at=date(2026, 4, 10),
    )

    async def fail_preview(*_args, **_kwargs):
        raise AssertionError("preview should not be called")

    monkeypatch.setattr(module, "build_auto_payment_preview", fail_preview)

    sent = await module.send_auto_payment_reminder_if_needed(
        user.id,
        date(2026, 4, 10),
        4,
    )

    assert sent is False
    assert not await NotificationMarks.filter(
        user_id=user.id,
        type=module.AUTO_PAYMENT_REMINDER_MARK_TYPE,
    ).exists()


@pytest.mark.asyncio
async def test_auto_payment_reminder_catchup_evening_window_sends_without_duplicates(
    monkeypatch,
):
    from bloobcat.db.users import Users

    module = _reload_module("bloobcat.tasks.auto_payment_reminders")
    target_expired = date(2026, 4, 10)
    active_user = await Users.create(
        id=4704,
        username="auto4704",
        full_name="Auto Reminder 4704",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4704",
        expired_at=target_expired,
    )
    missed_user = await Users.create(
        id=4705,
        username="auto4705",
        full_name="Auto Reminder 4705",
        is_registered=True,
        is_subscribed=True,
        renew_id="renew-4705",
        expired_at=target_expired,
    )

    sent_to: list[int] = []

    async def fake_preview(_user_obj):
        return types.SimpleNamespace(
            total_amount=2190.0,
            amount_external=1690.0,
            amount_from_balance=500.0,
        )

    async def fake_notify(user_obj, **_kwargs):
        sent_to.append(int(user_obj.id))
        return True

    monkeypatch.setattr(module, "build_auto_payment_preview", fake_preview)
    monkeypatch.setattr(module, "notify_auto_payment", fake_notify)
    _enable_yookassa_auto_renewal(monkeypatch)

    first_total = await module.auto_payment_reminders_once(
        datetime(2026, 4, 5, 20, 30, tzinfo=module.MOSCOW)
    )
    second_total = await module.auto_payment_reminders_once(
        datetime(2026, 4, 5, 20, 45, tzinfo=module.MOSCOW)
    )

    await Users.filter(id=active_user.id).delete()

    after_midnight_total = await module.auto_payment_reminders_once(
        datetime(2026, 4, 6, 0, 30, tzinfo=module.MOSCOW)
    )

    assert first_total == 2
    assert second_total == 0
    assert after_midnight_total == 0
    assert sent_to == [active_user.id, missed_user.id]

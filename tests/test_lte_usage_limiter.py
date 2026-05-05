from datetime import date
from pathlib import Path
import sys

import pytest
import pytest_asyncio
from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
    from tests._payment_test_stubs import install_stubs
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _sqlite_datetime_compat import register_sqlite_datetime_compat
    from _payment_test_stubs import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    restore_stubs = install_stubs()
    notifications_pkg = sys.modules.get("bloobcat.bot.notifications")
    if notifications_pkg is not None:
        notifications_pkg.__path__ = [
            str(
                Path(__file__).resolve().parents[1]
                / "bloobcat"
                / "bot"
                / "notifications"
            )
        ]
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
                        "bloobcat.db.notifications",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )

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
async def test_lte_half_threshold_marks_delivered_once(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users
    from bloobcat.tasks import lte_usage_limiter as limiter_module

    user = await Users.create(
        id=9801,
        username="lte9801",
        full_name="LTE 9801",
        is_registered=True,
        expired_at=date.today(),
    )

    sent: list[tuple[float, float, bool]] = []

    async def fake_notify(
        user_obj, used_gb: float, total_gb: float, is_trial: bool = False
    ):
        _ = user_obj
        sent.append((used_gb, total_gb, is_trial))
        return True

    monkeypatch.setattr(limiter_module, "notify_lte_half_limit", fake_notify)

    await limiter_module._notify_lte_thresholds(
        user=user,
        used_gb=6.0,
        total_gb=10.0,
        is_trial=False,
    )
    await limiter_module._notify_lte_thresholds(
        user=user,
        used_gb=7.0,
        total_gb=10.0,
        is_trial=False,
    )

    assert sent == [(6.0, 10.0, False)]
    marks = await NotificationMarks.filter(user_id=user.id, type="lte_usage").all()
    assert [(mark.key, mark.meta) for mark in marks] == [("half", "delivered")]


@pytest.mark.asyncio
async def test_lte_full_threshold_marks_trial_path_when_delivery_succeeds(
    monkeypatch,
):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users
    from bloobcat.tasks import lte_usage_limiter as limiter_module

    user = await Users.create(
        id=9802,
        username="lte9802",
        full_name="LTE 9802",
        is_registered=True,
        is_trial=True,
        expired_at=date.today(),
    )

    sent: list[tuple[float, float, bool]] = []

    async def fake_notify(
        user_obj, used_gb: float, total_gb: float, is_trial: bool = False
    ):
        _ = user_obj
        sent.append((used_gb, total_gb, is_trial))
        return True

    monkeypatch.setattr(limiter_module, "notify_lte_full_limit", fake_notify)

    await limiter_module._notify_lte_thresholds(
        user=user,
        used_gb=1.0,
        total_gb=1.0,
        is_trial=True,
    )
    await limiter_module._notify_lte_thresholds(
        user=user,
        used_gb=1.5,
        total_gb=1.0,
        is_trial=True,
    )

    assert sent == [(1.0, 1.0, True)]
    marks = await NotificationMarks.filter(user_id=user.id, type="lte_usage").all()
    assert [(mark.key, mark.meta) for mark in marks] == [("trial_full", "delivered")]


@pytest.mark.asyncio
async def test_lte_full_threshold_uses_configurable_trial_limit(monkeypatch):
    from bloobcat.db.notifications import NotificationMarks
    from bloobcat.db.users import Users
    from bloobcat.tasks import lte_usage_limiter as limiter_module

    user = await Users.create(
        id=9804,
        username="lte9804",
        full_name="LTE 9804",
        is_registered=True,
        is_trial=True,
        expired_at=date.today(),
    )

    sent: list[tuple[float, float, bool]] = []

    async def fake_notify(
        user_obj, used_gb: float, total_gb: float, is_trial: bool = False
    ):
        _ = user_obj
        sent.append((used_gb, total_gb, is_trial))
        return True

    monkeypatch.setattr(limiter_module, "notify_lte_full_limit", fake_notify)

    await limiter_module._notify_lte_thresholds(
        user=user,
        used_gb=2.0,
        total_gb=2.0,
        is_trial=True,
    )

    assert sent == [(2.0, 2.0, True)]
    marks = await NotificationMarks.filter(user_id=user.id, type="lte_usage").all()
    assert [(mark.key, mark.meta) for mark in marks] == [("trial_full", "delivered")]


@pytest.mark.asyncio
async def test_lte_user_notification_sends_topup_button(monkeypatch):
    from bloobcat.bot.notifications import lte as lte_module
    from bloobcat.db.users import Users

    user = await Users.create(
        id=9803,
        username="lte9803",
        full_name="LTE 9803",
        is_registered=True,
        language_code="ru",
    )

    sent: dict[str, object] = {}

    async def fake_button(text: str, url: str):
        sent["button"] = (text, url)
        return {"button": text, "url": url}

    class FakeBot:
        async def send_message(self, user_id, text, reply_markup=None):
            sent["message"] = (user_id, text, reply_markup)

    async def fake_reset(user_id: int):
        sent["reset"] = user_id
        return True

    monkeypatch.setattr(lte_module, "webapp_inline_button", fake_button)
    monkeypatch.setattr(lte_module, "bot", FakeBot())
    monkeypatch.setattr(lte_module, "reset_user_failed_count", fake_reset)

    delivered = await lte_module.notify_lte_half_limit(user, 8.0, 10.0)

    assert delivered is True
    assert sent["button"] == ("Пополнить LTE", "/subscription?lteTopup=1")
    user_id, text, reply_markup = sent["message"]
    assert user_id == user.id
    assert "LTE-трафик скоро закончится" in text
    assert "Использовано: 8/10 ГБ." in text
    assert reply_markup == {"button": "Пополнить LTE", "url": "/subscription?lteTopup=1"}
    assert sent["reset"] == user.id

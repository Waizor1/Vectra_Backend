"""Cron decay-push timing tests for the home-screen install promo task.

These tests deliberately mock the bot send so the schedule machinery can be
exercised without touching aiogram or running event loops longer than necessary.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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


def _utc(year, month, day, hour=12, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_next_attempt_due_at_first_attempt_is_24h_after_registration():
    from bloobcat.tasks.home_screen_install_promo import _next_attempt_due_at

    reg = _utc(2026, 5, 1)
    due = _next_attempt_due_at(promo_sent_count=0, registration_date=reg, last_sent_at=None)
    assert due == reg + timedelta(hours=24)


def test_next_attempt_due_at_second_attempt_is_7d_after_first_send():
    from bloobcat.tasks.home_screen_install_promo import _next_attempt_due_at

    reg = _utc(2026, 5, 1)
    first_sent = _utc(2026, 5, 2)
    due = _next_attempt_due_at(promo_sent_count=1, registration_date=reg, last_sent_at=first_sent)
    assert due == first_sent + timedelta(days=7)


def test_next_attempt_due_at_third_attempt_is_30d_after_second_send():
    from bloobcat.tasks.home_screen_install_promo import _next_attempt_due_at

    reg = _utc(2026, 5, 1)
    second_sent = _utc(2026, 5, 9)
    due = _next_attempt_due_at(promo_sent_count=2, registration_date=reg, last_sent_at=second_sent)
    assert due == second_sent + timedelta(days=30)


def test_next_attempt_due_at_returns_none_after_max_attempts():
    from bloobcat.tasks.home_screen_install_promo import (
        MAX_ATTEMPTS,
        _next_attempt_due_at,
    )

    reg = _utc(2026, 5, 1)
    last_sent = _utc(2026, 6, 8)
    assert (
        _next_attempt_due_at(
            promo_sent_count=MAX_ATTEMPTS,
            registration_date=reg,
            last_sent_at=last_sent,
        )
        is None
    )


@pytest.mark.asyncio
async def test_scan_skips_users_younger_than_24h():
    from bloobcat.db.users import Users
    from bloobcat.tasks.home_screen_install_promo import _scan_once

    # registered 12h ago — first attempt not yet due
    young = _utc(2026, 5, 12, 12, 0)
    now = young + timedelta(hours=12)
    await Users.create(
        id=10001,
        full_name="too-young",
        is_registered=True,
        registration_date=young,
    )

    with patch(
        "bloobcat.tasks.home_screen_install_promo.send_home_screen_install_promo",
        return_value=True,
    ) as mock_send:
        sent = await _scan_once(now=now)

    assert sent == 0
    mock_send.assert_not_called()
    refreshed = await Users.get(id=10001)
    assert refreshed.home_screen_promo_sent_count == 0


@pytest.mark.asyncio
async def test_scan_sends_first_attempt_after_24h():
    from bloobcat.db.users import Users
    from bloobcat.tasks.home_screen_install_promo import _scan_once

    reg = _utc(2026, 5, 1)
    now = reg + timedelta(hours=25)
    await Users.create(
        id=10002,
        full_name="ready",
        is_registered=True,
        registration_date=reg,
    )

    with patch(
        "bloobcat.tasks.home_screen_install_promo.send_home_screen_install_promo",
        return_value=True,
    ) as mock_send:
        sent = await _scan_once(now=now)

    assert sent == 1
    mock_send.assert_called_once()
    refreshed = await Users.get(id=10002)
    assert refreshed.home_screen_promo_sent_count == 1
    assert refreshed.home_screen_promo_sent_at is not None


@pytest.mark.asyncio
async def test_scan_respects_7d_gap_between_attempt_1_and_2():
    from bloobcat.db.users import Users
    from bloobcat.tasks.home_screen_install_promo import _scan_once

    reg = _utc(2026, 5, 1)
    first_sent = _utc(2026, 5, 2)
    # 6 days after first send — not yet due
    now = first_sent + timedelta(days=6)
    await Users.create(
        id=10003,
        full_name="too-soon-for-2nd",
        is_registered=True,
        registration_date=reg,
        home_screen_promo_sent_at=first_sent,
        home_screen_promo_sent_count=1,
    )

    with patch(
        "bloobcat.tasks.home_screen_install_promo.send_home_screen_install_promo",
        return_value=True,
    ) as mock_send:
        sent = await _scan_once(now=now)

    assert sent == 0
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_scan_stops_after_three_attempts():
    from bloobcat.db.users import Users
    from bloobcat.tasks.home_screen_install_promo import _scan_once

    reg = _utc(2026, 5, 1)
    third_sent = _utc(2026, 6, 9)
    # Way past 30d after the third attempt — still must not send a fourth.
    now = third_sent + timedelta(days=60)
    await Users.create(
        id=10004,
        full_name="exhausted",
        is_registered=True,
        registration_date=reg,
        home_screen_promo_sent_at=third_sent,
        home_screen_promo_sent_count=3,
    )

    with patch(
        "bloobcat.tasks.home_screen_install_promo.send_home_screen_install_promo",
        return_value=True,
    ) as mock_send:
        sent = await _scan_once(now=now)

    assert sent == 0
    mock_send.assert_not_called()
    refreshed = await Users.get(id=10004)
    assert refreshed.home_screen_promo_sent_count == 3


@pytest.mark.asyncio
async def test_scan_excludes_users_who_already_installed():
    from bloobcat.db.users import Users
    from bloobcat.tasks.home_screen_install_promo import _scan_once

    reg = _utc(2026, 5, 1)
    now = reg + timedelta(days=10)
    await Users.create(
        id=10005,
        full_name="installed",
        is_registered=True,
        registration_date=reg,
        home_screen_added_at=now - timedelta(days=2),
    )

    with patch(
        "bloobcat.tasks.home_screen_install_promo.send_home_screen_install_promo",
        return_value=True,
    ) as mock_send:
        sent = await _scan_once(now=now)

    assert sent == 0
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_scan_excludes_blocked_users():
    from bloobcat.db.users import Users
    from bloobcat.tasks.home_screen_install_promo import _scan_once

    reg = _utc(2026, 5, 1)
    now = reg + timedelta(days=2)
    await Users.create(
        id=10006,
        full_name="blocked",
        is_registered=True,
        registration_date=reg,
        is_blocked=True,
    )

    with patch(
        "bloobcat.tasks.home_screen_install_promo.send_home_screen_install_promo",
        return_value=True,
    ) as mock_send:
        sent = await _scan_once(now=now)

    assert sent == 0
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_scan_does_not_bump_counter_when_send_failed():
    """A failed send (Telegram blocked / network) leaves counters unchanged so
    the next scan retries the same attempt — no skipped step."""
    from bloobcat.db.users import Users
    from bloobcat.tasks.home_screen_install_promo import _scan_once

    reg = _utc(2026, 5, 1)
    now = reg + timedelta(hours=25)
    await Users.create(
        id=10007,
        full_name="send-fails",
        is_registered=True,
        registration_date=reg,
    )

    with patch(
        "bloobcat.tasks.home_screen_install_promo.send_home_screen_install_promo",
        return_value=False,
    ):
        sent = await _scan_once(now=now)

    assert sent == 0
    refreshed = await Users.get(id=10007)
    assert refreshed.home_screen_promo_sent_count == 0
    assert refreshed.home_screen_promo_sent_at is None

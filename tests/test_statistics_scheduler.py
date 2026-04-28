from __future__ import annotations

import importlib
from datetime import date, datetime

import pytest


statistics_scheduler = importlib.import_module("bloobcat.statistics.scheduler")


@pytest.mark.parametrize(
    ("raw_now", "expected"),
    [
        (
            datetime(2026, 4, 20, 23, 58, 59, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 4, 20, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
        (
            datetime(2026, 4, 20, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 4, 21, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
        (
            datetime(2026, 4, 20, 0, 0, 1, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 4, 20, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
    ],
)
def test_get_next_daily_time_uses_nearest_future_2359(raw_now, expected):
    assert statistics_scheduler.get_next_daily_time(raw_now) == expected


@pytest.mark.parametrize(
    ("raw_now", "expected"),
    [
        (
            datetime(2026, 4, 19, 23, 58, 59, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 4, 19, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
        (
            datetime(2026, 4, 19, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 4, 26, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
        (
            datetime(2026, 4, 20, 0, 0, 1, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 4, 26, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
    ],
)
def test_get_next_weekly_time_uses_nearest_future_sunday_2359(raw_now, expected):
    assert statistics_scheduler.get_next_weekly_time(raw_now) == expected


@pytest.mark.parametrize(
    ("raw_now", "expected"),
    [
        (
            datetime(2026, 4, 20, 12, 0, 0, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 4, 30, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
        (
            datetime(2026, 4, 30, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 5, 31, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
        (
            datetime(2026, 5, 1, 0, 0, 1, tzinfo=statistics_scheduler.MOSCOW),
            datetime(2026, 5, 31, 23, 59, 0, tzinfo=statistics_scheduler.MOSCOW),
        ),
    ],
)
def test_get_next_monthly_time_uses_nearest_future_month_end(raw_now, expected):
    assert statistics_scheduler.get_next_monthly_time(raw_now) == expected


@pytest.mark.asyncio
async def test_send_daily_statistics_returns_false_when_admin_delivery_fails(
    monkeypatch,
):
    scheduled: list[str] = []

    async def fake_daily_trends(target_date):
        return {"target_date": target_date.isoformat()}

    async def fake_send_admin_message(report):
        _ = report
        return False

    monkeypatch.setattr(
        statistics_scheduler.TrendsCalculator,
        "calculate_daily_trends",
        fake_daily_trends,
    )
    monkeypatch.setattr(
        statistics_scheduler.StatisticsFormatter,
        "format_daily_report",
        lambda trends: f"report:{trends['target_date']}",
    )
    monkeypatch.setattr(
        statistics_scheduler,
        "send_admin_message",
        fake_send_admin_message,
    )
    monkeypatch.setattr(
        statistics_scheduler,
        "schedule_next_daily_statistics",
        lambda: scheduled.append("daily"),
    )

    delivered = await statistics_scheduler.send_daily_statistics()

    assert delivered is False
    assert scheduled == ["daily"]


@pytest.mark.asyncio
async def test_send_monthly_statistics_uses_explicit_target_date(monkeypatch):
    scheduled: list[str] = []
    captured: list[tuple[int, int]] = []

    async def fake_monthly_trends(target_month, target_year):
        captured.append((target_month, target_year))
        return {"target_month": target_month, "target_year": target_year}

    async def fake_send_admin_message(report):
        _ = report
        return True

    monkeypatch.setattr(
        statistics_scheduler.TrendsCalculator,
        "calculate_monthly_trends",
        fake_monthly_trends,
    )
    monkeypatch.setattr(
        statistics_scheduler.StatisticsFormatter,
        "format_monthly_report",
        lambda trends: f"report:{trends['target_month']}/{trends['target_year']}",
    )
    monkeypatch.setattr(
        statistics_scheduler,
        "send_admin_message",
        fake_send_admin_message,
    )
    monkeypatch.setattr(
        statistics_scheduler,
        "schedule_next_monthly_statistics",
        lambda: scheduled.append("monthly"),
    )

    delivered = await statistics_scheduler.send_monthly_statistics(
        date(2026, 4, 30)
    )

    assert delivered is True
    assert captured == [(4, 2026)]
    assert scheduled == ["monthly"]

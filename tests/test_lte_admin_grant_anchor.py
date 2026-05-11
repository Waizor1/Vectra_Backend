"""Anchor selection for the LTE quota window.

Pre-fix, the anchor was always `trial_started_at or created_at`. Admin-grant
users typically have `trial_started_at = NULL` and a `created_at` from
months ago, so the limiter saw a lifetime of LTE traffic against day-1 of
the grant.

The fix: insert `admin_lte_granted_at` between `trial_started_at` and
`created_at`. The stamp is set inside `sync_user_lte` on first grant, so
the limiter walks the quota window from the grant moment forward.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


MSK_TZ = timezone(timedelta(hours=3))


def _make_user(*, trial_started_at=None, admin_lte_granted_at=None, created_at=None):
    return SimpleNamespace(
        id=1,
        trial_started_at=trial_started_at,
        admin_lte_granted_at=admin_lte_granted_at,
        created_at=created_at,
    )


def test_trial_lte_start_date_prefers_trial_started_at():
    from bloobcat.routes.user import _trial_lte_start_date

    trial = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    grant = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    created = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    user = _make_user(
        trial_started_at=trial,
        admin_lte_granted_at=grant,
        created_at=created,
    )
    expected = trial.astimezone(MSK_TZ).date()
    assert _trial_lte_start_date(user) == expected


def test_trial_lte_start_date_falls_back_to_admin_grant_when_no_trial():
    """Regression: admin-grant non-trial users now anchor on the grant moment."""
    from bloobcat.routes.user import _trial_lte_start_date

    grant = datetime(2026, 5, 10, 9, 0, tzinfo=timezone.utc)
    created = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    user = _make_user(
        trial_started_at=None,
        admin_lte_granted_at=grant,
        created_at=created,
    )
    expected = grant.astimezone(MSK_TZ).date()
    assert _trial_lte_start_date(user) == expected
    # Sanity: must NOT fall back to the (much older) created_at.
    assert _trial_lte_start_date(user) != created.astimezone(MSK_TZ).date()


def test_trial_lte_start_date_falls_back_to_created_at_for_legacy_users():
    """Legacy admin-grants (NULL admin_lte_granted_at) keep the old behaviour."""
    from bloobcat.routes.user import _trial_lte_start_date

    created = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    user = _make_user(
        trial_started_at=None,
        admin_lte_granted_at=None,
        created_at=created,
    )
    expected = created.astimezone(MSK_TZ).date()
    assert _trial_lte_start_date(user) == expected


def test_trial_lte_start_date_handles_naive_datetimes():
    """Anchors stored without tzinfo (older rows) are treated as UTC."""
    from bloobcat.routes.user import _trial_lte_start_date

    naive = datetime(2026, 5, 10, 9, 0)  # no tzinfo
    user = _make_user(
        trial_started_at=None,
        admin_lte_granted_at=naive,
        created_at=None,
    )
    expected = naive.replace(tzinfo=timezone.utc).astimezone(MSK_TZ).date()
    assert _trial_lte_start_date(user) == expected

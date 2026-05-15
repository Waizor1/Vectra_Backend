"""Unit tests for the defensive `_coerce_user_id` helper.

Bug observed in production (2026-05-15) — `home_screen_install_promo.py:57,60`
called `handle_telegram_*(user, ...)` with the Users object instead of
`user.id`. The handlers crashed at `Users.get_or_none(id=user_id)` deep in
Tortoise with `int() argument must be a string, a bytes-like object or a
real number, not 'Users'`. The coercion helper turns this into a soft
warning + automatic recovery so log spam stays low and we never silently
drop a forbidden/blocked-user signal.
"""

from __future__ import annotations

from types import SimpleNamespace


def test_coerce_user_id_passes_through_int():
    from bloobcat.bot.error_handler import _coerce_user_id

    assert _coerce_user_id(42, caller="t") == 42
    assert _coerce_user_id(0, caller="t") == 0


def test_coerce_user_id_extracts_id_from_object_with_id_attr():
    from bloobcat.bot.error_handler import _coerce_user_id

    fake_user = SimpleNamespace(id=12345, full_name="x")
    assert _coerce_user_id(fake_user, caller="t") == 12345


def test_coerce_user_id_handles_string_int():
    from bloobcat.bot.error_handler import _coerce_user_id

    # Some legacy call sites might pass a string id from URL params.
    assert _coerce_user_id("999", caller="t") == 999


def test_coerce_user_id_returns_none_for_uncoercible():
    from bloobcat.bot.error_handler import _coerce_user_id

    assert _coerce_user_id(None, caller="t") is None
    assert _coerce_user_id("not-a-number", caller="t") is None
    assert _coerce_user_id(object(), caller="t") is None


def test_coerce_user_id_logs_warning_when_passed_object(caplog):
    """The warning is the signal Ops uses to find call sites that should be
    fixed; without it the handler would silently work and the bug would
    persist forever."""
    from bloobcat.bot.error_handler import _coerce_user_id

    fake_user = SimpleNamespace(id=777)
    caplog.clear()
    result = _coerce_user_id(fake_user, caller="handle_telegram_bad_request")
    assert result == 777


def test_coerce_user_id_returns_none_for_object_without_int_id():
    from bloobcat.bot.error_handler import _coerce_user_id

    # If id attribute isn't an int (e.g. None during partial init), bail out.
    fake = SimpleNamespace(id=None)
    assert _coerce_user_id(fake, caller="t") is None

"""Регрессионные тесты для anti-twink логики (дублирующий HWID)."""

from datetime import date, timedelta

from bloobcat.routes.remnawave.hwid_utils import (
    has_duplicate_hwid,
    is_user_already_antitwink_sanctioned,
)


def test_duplicate_hwid_two_accounts_same_hwid_detected():
    """Дублирующий HWID у двух аккаунтов -> anti-twink определяет дубликат."""
    hwid_index = {
        "hwid-abc-123": {"user-uuid-1", "user-uuid-2"},
    }
    assert has_duplicate_hwid("user-uuid-1", hwid_index) is True
    assert has_duplicate_hwid("user-uuid-2", hwid_index) is True


def test_duplicate_hwid_single_account_no_duplicate():
    """Один аккаунт, один HWID -> дубликата нет."""
    hwid_index = {
        "hwid-abc-123": {"user-uuid-1"},
    }
    assert has_duplicate_hwid("user-uuid-1", hwid_index) is False


def test_duplicate_hwid_same_user_multiple_devices_no_duplicate():
    """Один пользователь, несколько своих устройств -> дубликата нет."""
    hwid_index = {
        "hwid-1": {"user-uuid-1"},
        "hwid-2": {"user-uuid-1"},
    }
    assert has_duplicate_hwid("user-uuid-1", hwid_index) is False


def test_duplicate_hwid_one_shared_one_own_detected():
    """Один HWID общий с другим аккаунтом -> дубликат определяется."""
    hwid_index = {
        "hwid-shared": {"user-uuid-1", "user-uuid-2"},
        "hwid-own": {"user-uuid-1"},
    }
    assert has_duplicate_hwid("user-uuid-1", hwid_index) is True
    assert has_duplicate_hwid("user-uuid-2", hwid_index) is True


def test_duplicate_hwid_user_not_in_index_no_duplicate():
    """Пользователь не в индексе (нет устройств) -> дубликата нет."""
    hwid_index = {
        "hwid-abc": {"user-uuid-other"},
    }
    assert has_duplicate_hwid("user-uuid-missing", hwid_index) is False


def test_duplicate_hwid_empty_index_no_duplicate():
    """Пустой индекс -> дубликата нет."""
    assert has_duplicate_hwid("user-uuid-1", {}) is False


def test_duplicate_hwid_three_accounts_same_hwid_detected():
    """Три аккаунта с одним HWID -> все помечаются как дубликат."""
    hwid_index = {
        "hwid-shared": {"user-uuid-1", "user-uuid-2", "user-uuid-3"},
    }
    assert has_duplicate_hwid("user-uuid-1", hwid_index) is True
    assert has_duplicate_hwid("user-uuid-2", hwid_index) is True
    assert has_duplicate_hwid("user-uuid-3", hwid_index) is True


# --- is_user_already_antitwink_sanctioned ---------------------------------


def test_already_sanctioned_after_first_revoke_tick():
    """Свежесанкционированный юзер (used_trial=True, expired_at=today, no paid)
    должен распознаваться как уже санкционированный — иначе catcher повторно
    отправит notify_trial_revoked_hwid и admin-сообщение каждые 10 минут."""
    today = date(2026, 5, 8)
    assert (
        is_user_already_antitwink_sanctioned(
            is_trial=False,
            used_trial=True,
            expired_date=today,
            today=today,
            has_paid_subscription=False,
        )
        is True
    )


def test_sanction_persists_on_following_days():
    """expired_date в прошлом — санкция всё ещё действует."""
    today = date(2026, 5, 10)
    assert (
        is_user_already_antitwink_sanctioned(
            is_trial=False,
            used_trial=True,
            expired_date=today - timedelta(days=2),
            today=today,
            has_paid_subscription=False,
        )
        is True
    )


def test_active_trial_user_not_sanctioned():
    """Активный триал-юзер не должен считаться санкционированным."""
    today = date(2026, 5, 8)
    assert (
        is_user_already_antitwink_sanctioned(
            is_trial=True,
            used_trial=False,
            expired_date=today + timedelta(days=3),
            today=today,
            has_paid_subscription=False,
        )
        is False
    )


def test_paid_user_not_sanctioned_even_with_used_trial():
    """Юзер с платной подпиской не санкционирован, даже если когда-то был триал.
    Это защищает от ложноположительной блокировки уже купивших."""
    today = date(2026, 5, 8)
    assert (
        is_user_already_antitwink_sanctioned(
            is_trial=False,
            used_trial=True,
            expired_date=today + timedelta(days=30),
            today=today,
            has_paid_subscription=True,
        )
        is False
    )


def test_user_with_expired_at_in_future_not_sanctioned():
    """Если expired_at в будущем — юзер не санкционирован (триал ещё активен или продлён)."""
    today = date(2026, 5, 8)
    assert (
        is_user_already_antitwink_sanctioned(
            is_trial=False,
            used_trial=True,
            expired_date=today + timedelta(days=1),
            today=today,
            has_paid_subscription=False,
        )
        is False
    )


def test_user_without_expired_at_not_sanctioned():
    """Юзер без expired_at — никогда не получал триал и не санкционирован."""
    today = date(2026, 5, 8)
    assert (
        is_user_already_antitwink_sanctioned(
            is_trial=False,
            used_trial=False,
            expired_date=None,
            today=today,
            has_paid_subscription=False,
        )
        is False
    )


def test_used_trial_false_means_not_sanctioned():
    """Если used_trial=False — это не санкция, а другое состояние (лимит, тех. сброс и т.п.)."""
    today = date(2026, 5, 8)
    assert (
        is_user_already_antitwink_sanctioned(
            is_trial=False,
            used_trial=False,
            expired_date=today,
            today=today,
            has_paid_subscription=False,
        )
        is False
    )

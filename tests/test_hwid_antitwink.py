"""Регрессионные тесты для anti-twink логики (дублирующий HWID)."""

from bloobcat.routes.remnawave.hwid_utils import has_duplicate_hwid


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

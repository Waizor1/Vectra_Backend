"""Регрессионные тесты для activation/registration path RemnaWave."""

from bloobcat.routes.remnawave.activation_logic import should_trigger_registration
from bloobcat.routes.remnawave.catcher import _extract_online_at
from bloobcat.routes.remnawave.hwid_utils import (
    count_active_devices,
    extract_hwid_from_device,
    parse_remnawave_devices,
)


# --- parse_remnawave_devices: разные форматы ответа RemnaWave ---


def test_parse_remnawave_devices_list_format():
    """Формат list: [{"hwid": "...", ...}, ...]."""
    raw = [
        {"hwid": "abc123", "deviceId": "dev1"},
        {"hwid": "xyz789"},
    ]
    result = parse_remnawave_devices(raw)
    assert len(result) == 2
    assert result[0]["hwid"] == "abc123"
    assert result[1]["hwid"] == "xyz789"


def test_parse_remnawave_devices_dict_response_list():
    """Формат dict: {"response": [...]}."""
    raw = {"response": [{"hwid": "r1"}, {"hwid": "r2"}]}
    result = parse_remnawave_devices(raw)
    assert len(result) == 2
    assert result[0]["hwid"] == "r1"
    assert result[1]["hwid"] == "r2"


def test_parse_remnawave_devices_dict_response_devices():
    """Формат dict: {"response": {"devices": [...]}}."""
    raw = {"response": {"devices": [{"hwid": "d1"}, {"hwid": "d2"}]}}
    result = parse_remnawave_devices(raw)
    assert len(result) == 2
    assert result[0]["hwid"] == "d1"
    assert result[1]["hwid"] == "d2"


def test_parse_remnawave_devices_dict_response_data():
    """Формат dict: {"response": {"data": [...]}}."""
    raw = {"response": {"data": [{"hwid": "da1"}, {"deviceId": "da2"}]}}
    result = parse_remnawave_devices(raw)
    assert len(result) == 2
    assert result[0]["hwid"] == "da1"
    assert result[1]["deviceId"] == "da2"


def test_parse_remnawave_devices_dict_response_data_devices():
    """Вложенный: {"response": {"data": {"devices": [...]}}}."""
    raw = {"response": {"data": {"devices": [{"hwid": "n1"}]}}}
    result = parse_remnawave_devices(raw)
    assert len(result) == 1
    assert result[0]["hwid"] == "n1"


def test_parse_remnawave_devices_none_returns_empty():
    """None -> []."""
    assert parse_remnawave_devices(None) == []


def test_parse_remnawave_devices_response_none_returns_empty():
    """{"response": None} -> []."""
    assert parse_remnawave_devices({"response": None}) == []


def test_parse_remnawave_devices_filters_non_dict_items():
    """Элементы не-dict отфильтровываются."""
    raw = [{"hwid": "ok"}, "string", 123, None, {"hwid": "ok2"}]
    result = parse_remnawave_devices(raw)
    assert len(result) == 2
    assert result[0]["hwid"] == "ok"
    assert result[1]["hwid"] == "ok2"


# --- extract_hwid_from_device ---


def test_extract_hwid_from_device_hwid_field():
    """Приоритет поля hwid."""
    assert extract_hwid_from_device({"hwid": "h1", "deviceId": "d1"}) == "h1"


def test_extract_hwid_from_device_device_id_fallback():
    """Fallback на deviceId."""
    assert extract_hwid_from_device({"deviceId": "d1"}) == "d1"


def test_extract_hwid_from_device_id_fallback():
    """Fallback на id."""
    assert extract_hwid_from_device({"id": "i1"}) == "i1"


def test_extract_hwid_from_device_empty_stripped_returns_none():
    """Пустые значения отбрасываются."""
    assert extract_hwid_from_device({"hwid": ""}) is None
    assert extract_hwid_from_device({"hwid": "   "}) is None


def test_extract_hwid_from_device_non_dict_returns_none():
    """Не-dict -> None."""
    assert extract_hwid_from_device(None) is None
    assert extract_hwid_from_device([]) is None


# --- count_active_devices ---


def test_count_active_devices_list_format():
    """Формат list: считаются только валидные активные."""
    raw = [
        {"hwid": "a1"},
        {"hwid": ""},
        {"deviceId": "d1"},
        {"hwid": "x", "status": "disabled"},
        {"id": "i1"},
    ]
    assert count_active_devices(raw) == 3


def test_count_active_devices_response_devices_format():
    """Формат response.devices сохраняет совместимость."""
    raw = {"response": {"devices": [{"hwid": "d1"}, {"hwid": "d2"}]}}
    assert count_active_devices(raw) == 2


def test_count_active_devices_excludes_deleted_status():
    """status=deleted/removed/inactive исключаются."""
    raw = [
        {"hwid": "ok"},
        {"hwid": "del", "status": "deleted"},
        {"hwid": "rem", "status": "removed"},
        {"hwid": "ina", "status": "inactive"},
        {"hwid": "dis", "status": "disabled"},
    ]
    assert count_active_devices(raw) == 1


def test_count_active_devices_excludes_flags():
    """isDeleted, isDisabled, deleted, deletedAt исключают запись."""
    raw = [
        {"hwid": "ok"},
        {"hwid": "x", "isDeleted": True},
        {"hwid": "y", "isDisabled": True},
        {"hwid": "z", "deleted": True},
        {"hwid": "w", "deletedAt": "2025-01-01T00:00:00Z"},
    ]
    assert count_active_devices(raw) == 1


def test_count_active_devices_string_flags_respected():
    """String flags from external APIs should be parsed safely."""
    raw = [
        {"hwid": "ok1", "isDeleted": "false"},
        {"hwid": "ok2", "isDisabled": "0"},
        {"hwid": "drop1", "deleted": "true"},
        {"hwid": "drop2", "isDeleted": "YES"},
    ]
    assert count_active_devices(raw) == 2


def test_count_active_devices_none_returns_zero():
    assert count_active_devices(None) == 0


def test_count_active_devices_dedup_same_hwid():
    """Одинаковый hwid в нескольких записях считается как одно устройство."""
    raw = [
        {"hwid": "same-hwid-1"},
        {"hwid": "same-hwid-1", "deviceId": "other-field"},
        {"deviceId": "same-hwid-1"},
    ]
    assert count_active_devices(raw) == 1


def test_count_active_devices_dedup_preserves_status_filter():
    """Дедупликация не ломает фильтрацию статусов: дубликат с excluded статусом не считается."""
    raw = [
        {"hwid": "h1"},
        {"hwid": "h1", "status": "deleted"},
        {"hwid": "h2"},
    ]
    assert count_active_devices(raw) == 2


# --- should_trigger_registration: onlineAt есть, connected_at пустой ---


def test_activation_online_at_present_connected_at_empty_triggers_registration():
    """Сценарий: onlineAt есть, connected_at пустой -> registration path срабатывает."""
    assert should_trigger_registration(
        online_at="2025-02-20T12:00:00Z",
        old_connected_at=None,
        is_registered=False,
        block_registration=False,
        is_antitwink_sanction=False,
    ) is True


def test_activation_connected_at_present_no_registration():
    """connected_at уже есть -> регистрация не срабатывает."""
    assert should_trigger_registration(
        online_at="2025-02-20T12:00:00Z",
        old_connected_at="2025-02-19T10:00:00",  # truthy
        is_registered=False,
        block_registration=False,
        is_antitwink_sanction=False,
    ) is False


def test_activation_already_registered_no_registration():
    """is_registered=True -> регистрация не срабатывает."""
    assert should_trigger_registration(
        online_at="2025-02-20T12:00:00Z",
        old_connected_at=None,
        is_registered=True,
        block_registration=False,
        is_antitwink_sanction=False,
    ) is False


def test_activation_block_registration_no_registration():
    """block_registration=True (дублирующий HWID) -> регистрация не срабатывает."""
    assert should_trigger_registration(
        online_at="2025-02-20T12:00:00Z",
        old_connected_at=None,
        is_registered=False,
        block_registration=True,
        is_antitwink_sanction=False,
    ) is False


def test_activation_antitwink_sanction_no_registration():
    """is_antitwink_sanction=True -> регистрация не срабатывает."""
    assert should_trigger_registration(
        online_at="2025-02-20T12:00:00Z",
        old_connected_at=None,
        is_registered=False,
        block_registration=False,
        is_antitwink_sanction=True,
    ) is False


def test_activation_no_online_at_no_registration():
    """onlineAt пустой -> регистрация не срабатывает."""
    assert should_trigger_registration(
        online_at=None,
        old_connected_at=None,
        is_registered=False,
        block_registration=False,
        is_antitwink_sanction=False,
    ) is False


def test_extract_online_at_prefers_top_level():
    data = {
        "onlineAt": "2026-02-20T12:00:00Z",
        "userTraffic": {
            "onlineAt": "2026-02-20T11:00:00Z",
            "firstConnectedAt": "2026-02-20T10:00:00Z",
        },
    }
    assert _extract_online_at(data) == "2026-02-20T12:00:00Z"


def test_extract_online_at_falls_back_to_first_connected_at():
    data = {
        "userTraffic": {
            "firstConnectedAt": "2026-02-19T07:30:00Z",
        }
    }
    assert _extract_online_at(data) == "2026-02-19T07:30:00Z"

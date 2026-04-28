"""Чистая логика принятия решений для activation/registration path в RemnaWave updater."""

from typing import Any


def should_trigger_registration(
    online_at: Any,
    old_connected_at: Any | None = None,
    is_registered: bool | None = None,
    block_registration: bool = False,
    is_antitwink_sanction: bool = False,
    key_activated: bool | None = None,
    has_hwid_device: bool = True,
) -> bool:
    """Определяет, должен ли сработать путь регистрации/активации ключа.

    Эталонная бизнес-логика: admin activation должен срабатывать не от одного
    факта onlineAt, а от первого реально зарегистрированного HWID-устройства.
    `connected_at` может появиться раньше HWID, поэтому при наличии нового
    `key_activated` старые флаги `old_connected_at`/`is_registered` не блокируют
    активацию.

    Arguments:
        online_at: значение onlineAt из RemnaWave (str или None)
        old_connected_at: legacy-флаг текущего connected_at
        is_registered: legacy-флаг регистрации
        block_registration: блокировка (например, из-за дублирующего HWID)
        is_antitwink_sanction: уже санкционирован за anti-twink ранее
        key_activated: отдельный флаг факта появления первого HWID
        has_hwid_device: есть ли у RW-user хотя бы один HWID

    Returns:
        True если activation/admin log должен сработать
    """
    if not online_at:
        return False
    if not has_hwid_device:
        return False
    if key_activated is None:
        if old_connected_at:
            return False
        if is_registered:
            return False
    elif key_activated:
        return False
    if block_registration or is_antitwink_sanction:
        return False
    return True

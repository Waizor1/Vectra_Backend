"""Чистая логика принятия решений для activation/registration path в RemnaWave updater."""

from typing import Any


def should_trigger_registration(
    online_at: Any,
    old_connected_at: Any,
    is_registered: bool,
    block_registration: bool,
    is_antitwink_sanction: bool,
) -> bool:
    """Определяет, должен ли сработать путь регистрации/активации при первом подключении.

    Сценарий: onlineAt есть, connected_at пустой -> registration/activation path.

    Arguments:
        online_at: значение onlineAt из RemnaWave (str или None)
        old_connected_at: текущий connected_at пользователя (datetime или None)
        is_registered: флаг is_registered
        block_registration: блокировка (например, из-за дублирующего HWID)
        is_antitwink_sanction: уже санкционирован за anti-twink ранее

    Returns:
        True если регистрация должна сработать
    """
    if not online_at:
        return False
    if old_connected_at:
        return False
    if is_registered or block_registration or is_antitwink_sanction:
        return False
    return True

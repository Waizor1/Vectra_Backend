import asyncio
from typing import Any, Dict, List, Optional, Union
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings
from bloobcat.logger import get_logger

logger = get_logger("remnawave_hwid_utils")


def parse_remnawave_devices(raw: Any) -> List[Dict[str, Any]]:
    """Единообразный парсинг ответа RemnaWave для устройств.

    Поддерживает форматы:
    - list: [{"hwid": "...", ...}, ...]
    - dict: {"response": [...]} | {"response": {"devices": [...]}} | {"response": {"data": [...]}}
    - вложенный: {"response": {"data": {"devices": [...]}}}
    """

    def _to_list(val: Any) -> List[Dict[str, Any]]:
        if isinstance(val, list):
            return [item for item in val if isinstance(item, dict)]
        return []

    def _device_keys_dict(val: Any) -> bool:
        if not isinstance(val, dict):
            return False
        return any(key in val for key in ("hwid", "deviceId", "id"))

    def _extract_from_dict(inner: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Поддерживаем несколько популярных форматов, встречающихся в разных версиях API.
        for key in ("devices", "data", "response", "items", "rows", "result"):
            maybe = inner.get(key)
            result = _to_list(maybe)
            if result:
                return result
            if isinstance(maybe, dict):
                nested = _extract_from_dict(maybe)
                if nested:
                    return nested
        # Fail-safe: иногда API может вернуть одно устройство как dict без списка.
        if _device_keys_dict(inner):
            return [inner]
        return []

    if raw is None:
        return []
    if isinstance(raw, list):
        return _to_list(raw)
    if isinstance(raw, dict):
        if "response" in raw:
            inner = raw["response"]
            if inner is None:
                return []
        else:
            inner = raw
        if isinstance(inner, list):
            return _to_list(inner)
        if isinstance(inner, dict):
            return _extract_from_dict(inner)
    return []


def extract_hwid_from_device(device: Dict[str, Any]) -> Optional[str]:
    """Извлекает HWID из записи устройства (hwid, deviceId, id). Пустые значения отбрасываются."""
    if not isinstance(device, dict):
        return None
    hwid = device.get("hwid") or device.get("deviceId") or device.get("id")
    if hwid is None:
        return None
    s = str(hwid).strip()
    return s if s else None


# Статусы, при которых устройство не считается активным (для devices_count)
_DEVICE_EXCLUDED_STATUSES = frozenset({"disabled", "deleted", "removed", "inactive"})


def _is_truthy_flag(value: Any) -> bool:
    """Safe bool parser for loose API payloads."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"1", "true", "yes", "y", "on"}
    return False


def _is_device_excluded_by_status(device: Dict[str, Any]) -> bool:
    """Проверяет, исключено ли устройство по явному статусу удаления/disabled."""
    if not isinstance(device, dict):
        return True
    status = device.get("status")
    if status is not None and str(status).strip().lower() in _DEVICE_EXCLUDED_STATUSES:
        return True
    if _is_truthy_flag(device.get("isDeleted")) or _is_truthy_flag(device.get("isDisabled")) or _is_truthy_flag(device.get("deleted")):
        return True
    deleted_at = device.get("deletedAt")
    if deleted_at is not None and deleted_at:
        return True
    return False


def count_active_devices(raw: Any) -> int:
    """Подсчёт валидных активных устройств из ответа RemnaWave.

    Учитывает: непустой hwid/deviceId/id; исключает записи с status=disabled/deleted/removed/inactive,
    isDeleted/isDisabled/deleted=true, deletedAt непустой.
    Считает только уникальные устройства по HWID (дедупликация).
    """
    devices = parse_remnawave_devices(raw)
    seen_hwids: set[str] = set()
    for d in devices:
        hwid = extract_hwid_from_device(d)
        if hwid and not _is_device_excluded_by_status(d):
            seen_hwids.add(hwid)
    return len(seen_hwids)


def has_duplicate_hwid(user_uuid: str, hwid_index: Dict[str, set]) -> bool:
    """Проверяет, есть ли у пользователя HWID, используемый другим аккаунтом (anti-twink).

    Arguments:
        user_uuid: UUID пользователя в RemnaWave
        hwid_index: словарь hwid -> set(user_uuids)

    Returns:
        True если хотя бы один HWID пользователя привязан к другому аккаунту
    """
    user_devices_hwid = [
        hwid_value
        for hwid_value, owners in hwid_index.items()
        if user_uuid in owners
    ]
    for hwid_value in user_devices_hwid:
        owners = hwid_index.get(hwid_value, set())
        if any(owner != user_uuid for owner in owners):
            return True
    return False


async def cleanup_user_hwid_devices(user_id: int, user_uuid: Union[str, object]) -> None:
    """
    Удаляет все HWID устройства пользователя в RemnaWave.

    Arguments:
        user_id: локальный ID пользователя для логирования
        user_uuid: UUID пользователя в RemnaWave (строка или object)
    """
    client = None
    try:
        client = RemnaWaveClient(
            remnawave_settings.url,
            remnawave_settings.token.get_secret_value()
        )
        raw_resp = await client.users.get_user_hwid_devices(str(user_uuid))
        logger.debug(f"Ответ API списка HWID устройств для пользователя {user_id}: {raw_resp}")
        devices = parse_remnawave_devices(raw_resp)
        logger.debug(f"Распарсированные HWID устройства для пользователя {user_id}: {devices}")

        for device in devices:
            hwid = extract_hwid_from_device(device)
            if not hwid:
                logger.warning(f"Нет HWID в записи устройства для пользователя {user_id}: {device}")
                continue
            payload = {"userUuid": str(user_uuid), "hwid": hwid}
            logger.debug(f"Параметры запроса удаления HWID для пользователя {user_id}: {payload}")
            try:
                delete_resp = await client.users.delete_user_hwid_device(str(user_uuid), hwid)
                logger.debug(f"Ответ API удаления HWID {hwid} для пользователя {user_id}: {delete_resp}")
            except Exception as exc:
                # Не логируем ошибки для уже удаленных устройств
                if 'A101' in str(exc) or 'Delete hwid user device error' in str(exc):
                    logger.debug(f"HWID {hwid} для пользователя {user_id} уже удален или не существует: {exc}")
                else:
                    logger.error(f"Ошибка удаления HWID {hwid} для пользователя {user_id}: {exc}")

    except Exception as e:
        logger.error(f"Ошибка очистки HWID для пользователя {user_id}: {e}")
    finally:
        if client:
            try:
                await client.close()
            except Exception as close_exc:
                logger.warning(f"Ошибка закрытия клиента RemnaWave: {close_exc}")


async def list_user_hwid_devices(user_uuid: str) -> List[Dict[str, Any]]:
    """Return parsed HWID devices for a single RemnaWave user UUID."""
    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        raw_resp = await client.users.get_user_hwid_devices(str(user_uuid))
        return parse_remnawave_devices(raw_resp)
    finally:
        await client.close()

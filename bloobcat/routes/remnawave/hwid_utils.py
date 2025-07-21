import asyncio
from typing import Union
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings
from bloobcat.logger import get_logger

logger = get_logger("remnawave_hwid_utils")

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
        # Парсим список устройств
        devices: list = []
        if isinstance(raw_resp, list):
            devices = raw_resp
        elif isinstance(raw_resp, dict):
            resp = raw_resp.get("response")
            if isinstance(resp, list):
                devices = resp
            elif isinstance(resp, dict) and isinstance(resp.get("devices"), list):
                devices = resp.get("devices")
        logger.debug(f"Распарсированные HWID устройства для пользователя {user_id}: {devices}")

        for device in devices:
            hwid = None
            if isinstance(device, dict):
                hwid = device.get("hwid") or device.get("deviceId") or device.get("id")
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
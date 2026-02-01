from typing import Iterable

from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings


logger = get_logger("remnawave_lte_utils")


def _extract_squad_uuids(raw: Iterable) -> list[str]:
    result: list[str] = []
    for item in raw or []:
        if isinstance(item, dict):
            uuid = item.get("uuid")
            if uuid:
                result.append(str(uuid))
        elif item:
            result.append(str(item))
    return result


async def set_lte_squad_status(
    user_uuid: str,
    *,
    enable: bool,
    client: RemnaWaveClient | None = None,
) -> bool:
    lte_uuid = remnawave_settings.lte_internal_squad_uuid
    if not lte_uuid:
        logger.debug("LTE squad UUID is not set, skipping update")
        return False

    close_client = False
    if client is None:
        client = RemnaWaveClient(
            remnawave_settings.url, remnawave_settings.token.get_secret_value()
        )
        close_client = True

    try:
        user_resp = await client.users.get_user_by_uuid(user_uuid)
        payload = user_resp.get("response") or {}
        squads = _extract_squad_uuids(payload.get("activeInternalSquads") or [])

        if enable:
            if lte_uuid in squads:
                return False
            squads.append(lte_uuid)
        else:
            if lte_uuid not in squads:
                return False
            squads = [uuid for uuid in squads if uuid != lte_uuid]

        await client.users.update_user(user_uuid, activeInternalSquads=squads)
        return True
    finally:
        if close_client:
            await client.close()

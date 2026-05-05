"""Cleanup task for expired temporary setup devices."""

from __future__ import annotations

import asyncio

from bloobcat.db.users import Users
from bloobcat.db.user_devices import UserDevice
from bloobcat.logger import get_logger
from bloobcat.services.temp_setup_links import is_temp_setup_token_expired

logger = get_logger("temp_setup_cleanup")
_INTERVAL_SECONDS = 60


async def cleanup_expired_temp_setup_for_user(user: Users) -> bool:
    if not user.temp_setup_expires_at:
        return False
    if not is_temp_setup_token_expired(user.temp_setup_expires_at):
        return False
    try:
        if user.temp_setup_device_id:
            device = await UserDevice.get_or_none(id=user.temp_setup_device_id)
            if device and not device.hwid:
                from bloobcat.services.device_service import delete_device, sync_device_hwid_from_remnawave

                if not await sync_device_hwid_from_remnawave(device):
                    await delete_device(device)
            user.temp_setup_device_id = None
        user.temp_setup_token = None
        user.temp_setup_expires_at = None
        await user.save(
            update_fields=[
                "temp_setup_token",
                "temp_setup_expires_at",
                "temp_setup_device_id",
            ]
        )
        return True
    except Exception as exc:
        logger.error("Failed to cleanup temp setup for user=%s: %s", user.id, exc)
        return False


async def cleanup_expired_temp_setup_devices() -> None:
    users = await Users.filter(temp_setup_expires_at__isnull=False).all()
    cleaned = 0
    for user in users:
        if await cleanup_expired_temp_setup_for_user(user):
            cleaned += 1
    if cleaned:
        logger.info("Cleaned up %s expired temp setup(s)", cleaned)


async def run_temp_setup_cleanup_scheduler() -> None:
    logger.info("Starting temp setup cleanup scheduler interval=%ss", _INTERVAL_SECONDS)
    while True:
        try:
            await cleanup_expired_temp_setup_devices()
        except Exception as exc:
            logger.error("Temp setup cleanup scheduler failed: %s", exc)
        await asyncio.sleep(_INTERVAL_SECONDS)

import asyncio

from bloobcat.db.users import Users
from bloobcat.bot.notifications.admin import send_admin_message, write_to
from bloobcat.logger import get_logger

logger = get_logger("trial_active_tariff_fix")


async def fix_trial_with_active_tariff_once() -> int:
    """
    Снимает is_trial у пользователей, у которых одновременно есть active_tariff.
    Возвращает количество исправленных пользователей.
    """
    candidates = await Users.filter(is_trial=True, active_tariff_id__not_isnull=True)
    if not candidates:
        return 0

    preview_ids = [str(user.id) for user in candidates[:10]]
    for user in candidates:
        user.is_trial = False
        user.used_trial = True
    await Users.bulk_update(candidates, fields=["is_trial", "used_trial"])
    try:
        preview_text = ", ".join(preview_ids) if preview_ids else "—"
        text = (
            "⚠️ Исправлены пользователи с is_trial и активным тарифом.\n"
            f"Всего: <b>{len(candidates)}</b>\n"
            f"Примеры ID: <code>{preview_text}</code>\n"
            "#trial #fix"
        )
        await send_admin_message(text=text, reply_markup=await write_to(candidates[0].id))
    except Exception as e:
        logger.error(f"Failed to send trial/active_tariff fix notification: {e}")
    return len(candidates)


async def run_trial_active_tariff_fix_scheduler(interval_seconds: int = 3600):
    logger.info(
        "Starting trial/active_tariff fixer (interval: %ss)", interval_seconds
    )
    while True:
        try:
            fixed = await fix_trial_with_active_tariff_once()
            if fixed:
                logger.info("Fixed %s users with is_trial + active_tariff", fixed)
        except Exception as e:
            logger.error(f"Error in trial/active_tariff fixer: {e}")
        await asyncio.sleep(interval_seconds)

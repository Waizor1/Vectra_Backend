from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger

logger = get_logger("notifications.trial.end")

async def notify_trial_ended(user):
    """
    Уведомляет пользователя о завершении пробного периода
    """
    logger.info(f"Отправка уведомления о завершении пробного периода пользователю {user.id}")
    text = """🔥 Ваш пробный период был завершен!
❗Вы можете продлить бесплатный тест период, напишите нам @BlubCatVPN_support
💸 Для продления подписки нажмите на кнопку «продлить подписку»"""
    await bot.send_message(
        user.id,
        text,
        reply_markup=await webapp_inline_button("Продлить подписку", "pay")
    ) 
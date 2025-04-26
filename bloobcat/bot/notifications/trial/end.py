from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger

logger = get_logger("notifications.trial.end")

async def notify_trial_ended(user):
    """
    Уведомляет пользователя о завершении пробного периода
    """
    logger.info(f"Отправка уведомления о завершении пробного периода пользователю {user.id}")
    text = (
        f"👋 Привет, {user.full_name}! Ваш пробный период завершен. 🎉\n"
        "Не упустите возможность продления и получите эксклюзивные условия! 🔥\n"
        "Напишите в поддержку @BlubCatVPN_support или продлите прямо сейчас."
    )
    await bot.send_message(
        user.id,
        text,
        reply_markup=await webapp_inline_button("Продлить сейчас", "pay")
    ) 
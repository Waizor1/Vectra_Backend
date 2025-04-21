from cyberdog.bot import bot
from cyberdog.bot.keyboard import webapp_inline_button
from cyberdog.db.users import Users


async def alerts_worker():
    users = await Users.all()
    for user in users:
        expires_days = user.expires()
        if expires_days is not None and 1 <= expires_days <= 3:
            await bot.send_message(
                user.id,
                f"❗ Ваш тариф истечет через {expires_days} {'день' if expires_days == 1 else 'дня' if expires_days == 2 else 'дня'}. Пожалуйста, продлите подписку в личном кабинете.",
                reply_markup=webapp_inline_button("Продлить подписку", "pay"),
            )

from datetime import datetime

from aiogram.exceptions import TelegramAPIError
from pytz import UTC

from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users


async def hours():
    users = await Users.filter(
        is_registered=False,
        is_sended_notification_connect=False,
    )
    for user in users:
        expired_at_hours = (
            datetime.now(UTC) - user.registration_date
        ).total_seconds() // 3600

        if expired_at_hours >= 1:
            user.is_sended_notification_connect = True
            await user.save()
            try:
                await bot.send_message(
                    chat_id=user.id,
                    text="""Здравствуйте!

Видим, что вас заинтересовал наш VPN сервис - BlubCat VPN.

Вы прошли авторизацию в боте, но не взяли бесплатный пробный период.
Подскажите, пожалуйста, какие сложности или вопросы у вас имеются?

Вы всегда можете написать в нашу службу технической поддержки
- @BlubCatVPN_support
Будем всегда рады вам помочь!

С уважением, команда BlubCat VPN.""",
                    reply_markup=await webapp_inline_button("Подключиться"),
                )
            except TelegramAPIError:
                pass

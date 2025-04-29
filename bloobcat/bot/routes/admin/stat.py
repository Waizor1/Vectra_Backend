from datetime import datetime, timedelta

from aiogram.dispatcher.router import Router
from aiogram.filters.command import Command, CommandObject
from aiogram.types.message import Message
from pytz import UTC

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.db.users import Users

router = Router()


@router.message(Command("stat"), IsAdmin())
async def admin_stat(message: Message, command: CommandObject):
    utm = command.args
    amount = await Users.filter(utm=utm).count()
    registered = await Users.filter(utm=utm, is_registered=True).count()
    payed = 0
    for user in await Users.filter(utm=utm):
        expires_days = user.expires()
        if expires_days is not None and expires_days > 3:
            payed += 1

    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    percent_registered = amount and registered / amount * 100 or 0
    percent_payed = registered and payed / registered * 100 or 0

    stats_message = (
        f"📊 <b>Статистика по UTM:</b> <code>{utm}</code>\n"
        f"<i>отчет на {now}</i>\n\n"
        f"👥 Всего зашли: <b>{amount}</b>\n"
        f"✅ Активировали: <b>{registered}</b> (<i>{percent_registered:.1f}%</i>)\n"
        f"💰 Оплатили: <b>{payed}</b> (<i>{percent_payed:.1f}%</i>)"
    )
    await message.answer(stats_message, parse_mode="HTML")


@router.message(Command("online"), IsAdmin())
async def online_(message: Message):
    m = await message.answer("⏳ <i>Подождите...</i>", parse_mode="HTML")

    active_users = await Users.filter(
        connected_at__gte=datetime.now(UTC) - timedelta(minutes=3)
    )
    i = len(active_users)

    await m.edit_text(f"👥 <b>Сейчас онлайн:</b> <code>{i}</code>", parse_mode="HTML")

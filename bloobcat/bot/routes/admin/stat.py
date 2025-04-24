
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

    await message.answer(f"""Статистика {utm}
Всего зашли: {amount}
Активировали: {registered}
Оплатили: {payed}""")


@router.message(Command("online"), IsAdmin())
async def online_(message: Message):
    m = await message.answer("Подождите...")

    active_users = await Users.filter(
        connected_at__gte=datetime.now(UTC) - timedelta(minutes=3)
    )
    i = len(active_users)

    await m.edit_text(f"Онлайн: {i}")

from datetime import datetime, timedelta

from aiogram.dispatcher.router import Router
from aiogram.filters.command import Command, CommandObject
from aiogram.types.message import Message
from pytz import UTC

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments

router = Router()


@router.message(Command("stat"), IsAdmin())
async def admin_stat(message: Message, command: CommandObject):
    utm = command.args
    amount = await Users.filter(utm=utm).count()
    registered = await Users.filter(utm=utm, is_registered=True).count()
    payed = 0
    # Count how many registered users have at least one successful payment
    registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
    if registered_ids:
        paid_user_ids = await ProcessedPayments.filter(
            user_id__in=registered_ids, status="succeeded"
        ).values_list("user_id", flat=True)
        payed = len(set(paid_user_ids))
    else:
        payed = 0

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

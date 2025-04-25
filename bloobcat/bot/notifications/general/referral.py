from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.general.referral")

async def on_referral_payment(user: Users, referral: Users, amount: int):
    to_add = int(amount * user.referral_percent() / 100)
    user.balance += to_add
    await user.save()
    lang = get_user_locale(user)
    if lang == 'ru':
        logger.info(f"Начисление реферального бонуса пользователю {user.id} в размере {to_add} руб. за оплату реферала {referral.id} на сумму {amount} руб.")
        text = f"""💰Ваш реферал {referral.name()} совершил оплату на сумму {amount} руб.
Ваш реф процент {user.referral_percent()}%
Вам зачислено {to_add} руб"""
        button = await webapp_inline_button()
    else:
        logger.info(f"Crediting referral bonus to user {user.id}: {to_add} RUB for referral {referral.id}'s payment of {amount} RUB.")
        text = f"""💰Your referral {referral.name()} has paid {amount} RUB.
Your referral rate is {user.referral_percent()}%.
You have been credited {to_add} RUB."""
        button = await webapp_inline_button("Dashboard")
    await bot.send_message(
        user.id,
        text,
        reply_markup=button,
    )

async def on_referral_registration(user: Users, referral: Users):
    lang = get_user_locale(user)
    if lang == 'ru':
        logger.info(f"Отправка уведомления о регистрации реферала {referral.id} пользователю {user.id}")
        text = f"""🎉Ваш реферал {referral.name()} зарегистрировался в сервисе.
Вы получили 7 дней подписки бесплатно!"""
        button = await webapp_inline_button()
    else:
        logger.info(f"Sending referral registration notification to user {user.id} for referral {referral.id}")
        text = f"""🎉Your referral {referral.name()} has registered in our service.
You have been awarded a 7-day free subscription!"""
        button = await webapp_inline_button("Dashboard")
    await bot.send_message(
        user.id,
        text,
        reply_markup=button,
    ) 
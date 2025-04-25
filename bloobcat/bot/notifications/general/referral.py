from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("notifications.general.referral")

async def on_referral_payment(user: Users, referral: Users, amount: int):
    to_add = int(amount * user.referral_percent() / 100)
    user.balance += to_add
    await user.save()
    logger.info(f"Начисление реферального бонуса пользователю {user.id} в размере {to_add} руб. за оплату реферала {referral.id} на сумму {amount} руб.")
    text = f"""💰Ваш реферал {referral.name()}\nсовершил оплату на сумму {amount} руб.\n\nВаш реф процент {user.referral_percent()}%\nВам зачислено {to_add} руб """
    await bot.send_message(
        user.id,
        text,
        reply_markup=await webapp_inline_button(),
    )

async def on_referral_registration(user: Users, referral: Users):
    logger.info(f"Отправка уведомления о регистрации реферала {referral.id} пользователю {user.id}")
    text = f"""🎉Ваш реферал {referral.name()} зарегистрировался в нашем сервисе.\nВы получили 7 дней подписки бесплатно!"""
    await bot.send_message(
        user.id,
        text,
        reply_markup=await webapp_inline_button("Личный кабинет"),
    ) 
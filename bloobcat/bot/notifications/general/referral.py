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
        logger.info(f"Реферальный бонус: пользователь {user.id} получил {to_add}₽ за оплату реферала {referral.id} на {amount}₽")
        text = (
            f"🎉 Привет, {user.full_name}! Ваш реферал {referral.name()} оплатил подписку на {amount}₽.\n"
            f"Вы получили {to_add}₽ на баланс. Спасибо за рекомендацию! 🎊"
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        logger.info(f"Crediting referral bonus to user {user.id}: {to_add} RUB for referral {referral.id}'s payment of {amount} RUB.")
        text = (
            f"🎉 Hi {user.full_name}! Your referral {referral.name()} just paid {amount} RUB.\n"
            f"You've been credited {to_add} RUB. Thanks for spreading the word! 🎊"
        )
        button = await webapp_inline_button("Dashboard")
    await bot.send_message(
        user.id,
        text,
        reply_markup=button,
    )

async def on_referral_registration(user: Users, referral: Users):
    lang = get_user_locale(user)
    if lang == 'ru':
        logger.info(f"Реферальная регистрация: пользователь {user.id} получил 7 дней подписки за регистрацию реферала {referral.id}")
        text = (
            f"🎉 Привет, {user.full_name}! Ваш реферал {referral.name()} только что зарегистрировался.\n"
            "Вы получили 7-дневный бесплатный доступ. Наслаждайтесь тестированием VPN!"
        )
        button = await webapp_inline_button("Активировать пробную")
    else:
        logger.info(f"Sending referral registration notification to user {user.id} for referral {referral.id}")
        text = (
            f"🎉 Hi {user.full_name}! Your referral {referral.name()} just signed up.\n"
            "You've been awarded a 7-day free trial. Enjoy testing our VPN!"
        )
        button = await webapp_inline_button("Activate Trial")
    await bot.send_message(
        user.id,
        text,
        reply_markup=button,
    )

async def on_referral_prompt(user: Users, days: int):
    """Уведомление для пользователей, чтобы пригласить друга и получить бонусы"""
    lang = get_user_locale(user)
    if lang == 'ru':
        text = (
            f"🎉 Привет, {user.full_name}! Вы уже с нами {days} дней. "
            "Пригласите друга и получите бонусы в реферальной программе!"
        )
        button = await webapp_inline_button("Реферальная программа", "ref")
    else:
        text = (
            f"🎉 Hi {user.full_name}! You've been with us for {days} days. "
            "Invite a friend and earn rewards in our referral program!"
        )
        button = await webapp_inline_button("Referral Program", "ref")
    logger.info(f"Отправка реферального напоминания пользователю {user.id} ({days} дней)")
    await bot.send_message(
        user.id,
        text,
        reply_markup=button,
    ) 
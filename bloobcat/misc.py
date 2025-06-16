from bloobcat.bot.bot import get_bot_username
from bloobcat.db.users import Users


async def generate_user_report(user: Users):
    referrer = await user.referrer()
    return f"""
*******************
Пользователь: {user.name}
Имя: {user.full_name}
👤 ID: {user.id}
⬇️ Дата активации бота: [00.00.24 - 00:00]
🟢 Дата активации ключа: [00.00.24 - 00:00] / нет
⌛️Остаток дней: {user.expires() if user.expires() is not None else 'нет данных'}
💲Автоплатежи: {"да" if user.is_subscribed else "нет"}

Кол-во оплат: 0
Сумма оплат: 0 P

Реферер: {referrer.name() if referrer else "нет"}
Кол-во рефералов: {await user.referrals()}
Реферальный процент: {user.referral_percent()}%
Бонусный баланс: {user.balance} P

Реферальная ссылка: https://t.me/{await get_bot_username()}/Connect?startapp={user.id}
"""

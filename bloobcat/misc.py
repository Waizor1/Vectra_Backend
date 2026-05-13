from urllib.parse import quote

from bloobcat.bot.bot import get_bot_username
from bloobcat.db.users import Users


async def _build_referral_link(user_id: int) -> str:
    # Admin user-report referral link. Mirrors `build_referral_link` and goes
    # through the bot chat (/start) so the invitee subscribes to the bot
    # before any trial-granted notification fires.
    return f"https://t.me/{await get_bot_username()}?start={quote(str(user_id), safe='')}"


async def generate_user_report(user: Users):
    referrer = await user.referrer()
    referral_link = await _build_referral_link(int(user.id))
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

Реферальная ссылка: {referral_link}
"""

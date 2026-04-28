from urllib.parse import quote

from bloobcat.bot.bot import get_bot_username
from bloobcat.db.users import Users
from bloobcat.settings import telegram_settings


async def _build_referral_link(user_id: int) -> str:
    webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
    if webapp_url and webapp_url.lower().startswith("https://"):
        sep = "&" if "?" in webapp_url else "?"
        return f"{webapp_url.rstrip('/')}{sep}startapp={quote(str(user_id), safe='')}"

    return f"https://t.me/{await get_bot_username()}?start={user_id}"


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

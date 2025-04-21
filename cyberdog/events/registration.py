from datetime import datetime, timedelta

from pytz import UTC

from cyberdog.bot.notifications import admin
from cyberdog.bot.notifications import user as user_notifications
from cyberdog.db.users import Users


# async def registration(user: Users):
#     user.is_registered = True
#     user.activation_date = datetime.now(UTC)
#     user.expired_at = datetime.today() + timedelta(days=3)

#     referrer = await user.referrer()
#     if referrer:
#         if not referrer.expired_at():
#             await referrer.extend_subscription(7)
#         await user_notifications.on_referral_registration(referrer, user)
#         await referrer.save()

#     await user.save()
#     await user_notifications.on_activated_key(user)
#     await admin.on_activated_key(user)


async def on_activation_bot(user: Users):
    await admin.on_activated_bot(user)

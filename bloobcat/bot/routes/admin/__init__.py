from aiogram import Router

# from .change import router as change_router
from .send import router as send_router
from .stat import router as stat_router
from .check_subs import router as check_subs_router
from .utm_generator import router as utm_generator_router
from .menu import router as menu_router
from .test_notifications import router as test_notifications_router
from .blocked_users import router as blocked_users_router

admin_router = Router()

admin_router.include_router(send_router)
admin_router.include_router(stat_router)
admin_router.include_router(check_subs_router)
admin_router.include_router(utm_generator_router)
admin_router.include_router(menu_router)
admin_router.include_router(test_notifications_router)
admin_router.include_router(blocked_users_router)


# @admin_router.message()
# async def echo(message: Message):
#     await message.answer("Я не понимаю тебя, для начала напиши /start 😐")

from aiogram import Router

# Новое админ меню
from .admin_menu import router as admin_menu_router
from .navigation import router as navigation_router
from .user_management import router as user_management_router

# Существующие роутеры (для совместимости)
from .change import router as change_router
from .send import router as send_router
from .stat import router as stat_router
from .check_subs import router as check_subs_router
from .utm_generator import router as utm_generator_router
from .menu import router as menu_router
from .test_notifications import router as test_notifications_router
from .blocked_users import router as blocked_users_router
from .statistics_commands import router as statistics_commands_router

admin_router = Router()

# Новое админ меню (приоритет)
admin_router.include_router(admin_menu_router)
admin_router.include_router(send_router)  # Должен быть перед navigation_router для FSM

# Статистика (должна быть перед navigation_router для правильной обработки page_ callback'ов)
admin_router.include_router(stat_router)

admin_router.include_router(navigation_router)
admin_router.include_router(user_management_router)

# Существующие роутеры
admin_router.include_router(change_router)
admin_router.include_router(check_subs_router)
admin_router.include_router(utm_generator_router)
admin_router.include_router(menu_router)
admin_router.include_router(test_notifications_router)
admin_router.include_router(blocked_users_router)
admin_router.include_router(statistics_commands_router)


# @admin_router.message()
# async def echo(message: Message):
#     await message.answer("Я не понимаю тебя, для начала напиши /start 😐")

from aiogram import Router

from .admin import admin_router
from .start import router as start_router

main_router = Router()

main_router.include_router(start_router)
main_router.include_router(admin_router)

from aiogram import Dispatcher, types
from fastapi import APIRouter, BackgroundTasks # type: ignore

from cyberdog.bot.bot import bot
from cyberdog.settings import telegram_settings

router = APIRouter()

# Создаем диспетчер, но не включаем маршрутизаторы сразу
dp = Dispatcher()

# Флаг для отслеживания, был ли уже настроен маршрутизатор
_router_setup_done = False

def setup_router():
    """
    Настраивает маршрутизатор для бота.
    Эта функция должна быть вызвана только один раз.
    """
    global _router_setup_done
    
    # Если маршрутизатор уже настроен, ничего не делаем
    if _router_setup_done:
        return
    
    # Импортируем main_router внутри функции
    from cyberdog.bot.routes import main_router
    
    # Включаем маршрутизатор в диспетчер
    dp.include_router(main_router)
    
    # Устанавливаем флаг, что маршрутизатор настроен
    _router_setup_done = True


@router.post("/webhook/{tg_secret}")
async def bot_webhook(
    update: dict, tg_secret: str, background_tasks: BackgroundTasks
):
    if tg_secret != telegram_settings.webhook_secret:
        return {"error": "Invalid secret"}
    telegram_update = types.Update(**update)
    background_tasks.add_task(dp.feed_update, bot=bot, update=telegram_update)
    return

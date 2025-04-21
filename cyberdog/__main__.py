import asyncio
from contextlib import asynccontextmanager

import uvicorn # type: ignore
from aerich import Command # type: ignore
from aiogram.types import BotCommand
from fastadmin import fastapi_app as admin_app # type: ignore
from fastapi import FastAPI # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from tortoise.contrib.fastapi import RegisterTortoise # type: ignore

from cyberdog.bot import bot, router, setup_router
from cyberdog.clients import TORTOISE_ORM
from cyberdog.db.admins import Admin
from cyberdog.online import online_worker_tasks
from cyberdog.routes import main_router, include_bot_router
from cyberdog.routes import app_info # Добавляем импорт нового роутера
from cyberdog.schedules import start_scheduler
from cyberdog.settings import script_settings, telegram_settings
from cyberdog.logger import get_logger

# Получаем основной логгер приложения
logger = get_logger("cyberdog")

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    logger.info("Запуск приложения...")
    start_scheduler()

    command = Command(tortoise_config=TORTOISE_ORM, location="migrations")
    logger.info("Ожидание готовности базы данных...")
    await asyncio.sleep(5)  # Даем базе данных время на инициализацию
    
    try:
        logger.info("Инициализация базы данных...")
        await command.init()
        
        logger.info("Применение миграций...")
        await command.upgrade(run_in_transaction=True)
        logger.info("Применение миграций завершено")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {str(e)}", exc_info=True)
        raise

    await bot.set_my_commands(
        [BotCommand(command="/start", description="Перезапуск бота")]
    )
    await Admin.init()
    logger.info("Инициализация бота завершена")

    print(
        script_settings.api_url
        + "/webhook/"
        + telegram_settings.webhook_secret
    )
    
    online_tasks = await online_worker_tasks()
    logger.info("Фоновые задачи запущены")
    
    async with RegisterTortoise(
        fastapi_app,
        config=TORTOISE_ORM,
        add_exception_handlers=True,
    ):
        yield
    
    for task in online_tasks:
        task.cancel()
    logger.info("Приложение остановлено")


app = FastAPI(lifespan=lifespan, openapi_url=None)

origins = [
    "https://ttestapp.guarddogvpn.com",
    "https://app.guarddogvpn.com",
    "https://*.trycloudflare.com",       # для тестовых туннелей
    "https://*.cloudflare.com"           # для постоянных туннелей
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,        # разрешаем только конкретные домены
    allow_credentials=True,       # включаем учетные данные
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,               # кэширование preflight запросов на 24 часа
    allow_origin_regex="https://.*\\.trycloudflare\\.com"  # разрешаем все поддомены cloudflare
)

app.mount("/admin", admin_app)
setup_router()
include_bot_router()
app.include_router(router)
app.include_router(main_router)
app.include_router(app_info.router) # Регистрируем новый роутер


async def run_server():
    config = uvicorn.Config(
        "cyberdog.__main__:app",
        port=33083,
        reload=script_settings.dev,
        host="0.0.0.0",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    tasks = []
    tasks.append(asyncio.create_task(run_server()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Завершение работы скрипта.")

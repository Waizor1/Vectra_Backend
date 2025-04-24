import asyncio
from contextlib import asynccontextmanager

import uvicorn # type: ignore
from aerich import Command # type: ignore
from aiogram.types import BotCommand
from fastadmin import fastapi_app as admin_app # type: ignore
from fastapi import FastAPI # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from tortoise.contrib.fastapi import RegisterTortoise # type: ignore

from bloobcat.bot import bot, router, setup_router
from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.admins import Admin
from bloobcat.routes import main_router, include_bot_router
from bloobcat.routes import app_info # Добавляем импорт нового роутера
from bloobcat.schedules import start_scheduler
from bloobcat.settings import script_settings, telegram_settings
from bloobcat.logger import get_logger

# Получаем основной логгер приложения
logger = get_logger("bloobcat")

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    command = Command(tortoise_config=TORTOISE_ORM, location="migrations")
    # Небольшая задержка перед инициализацией миграций
    await asyncio.sleep(5)
    
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
    
    # Запуск фоновых задач после инициализации БД и бота
    async with RegisterTortoise(
        fastapi_app,
        config=TORTOISE_ORM,
        add_exception_handlers=True,
    ):
        start_scheduler()
        logger.info("Фоновые задачи запущены")
        yield
    
    # Закрытие всех клиентов RemnaWave при завершении работы
    try:
        # Закрываем основной клиент из routes/user.py
        from bloobcat.routes.user import close_remnawave_client
        await close_remnawave_client()
        
        # Закрываем синглтон-клиент из процессора, если он был создан
        try:
            from bloobcat.routes.remnawave.catcher import remnawave
            if remnawave and remnawave.session:
                logger.info("Закрытие клиента RemnaWave из catcher.py")
                await remnawave.close()
        except (ImportError, AttributeError) as e:
            logger.warning(f"Не удалось закрыть remnawave клиент из catcher.py: {e}")
        
        # Попытка закрыть клиент из remnawave_processor, если он существует
        try:
            import sys
            if 'bloobcat.processing.remnawave_processor' in sys.modules:
                remnawave_module = sys.modules.get('bloobcat.processing.remnawave_processor')
                if hasattr(remnawave_module, 'remnawave_client_instance') and remnawave_module.remnawave_client_instance:
                    logger.info("Закрытие клиента RemnaWave из remnawave_processor.py")
                    await remnawave_module.remnawave_client_instance.close()
        except Exception as e:
            logger.warning(f"Не удалось закрыть remnawave_client_instance: {e}")

        logger.info("Все клиенты RemnaWave успешно закрыты")
    except Exception as e:
        logger.error(f"Ошибка при закрытии клиентов RemnaWave: {e}")

    logger.info("Приложение остановлено")


app = FastAPI(lifespan=lifespan, openapi_url=None)

origins = [
    "https://ttestapp.guarddogvpn.com",
    "https://app.guarddogvpn.com", 
    "https://app.starmy.store",
    "https://api.starmy.store",
    "https://*.trycloudflare.com",
    "https://*.cloudflare.com"
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
        "bloobcat.__main__:app",
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

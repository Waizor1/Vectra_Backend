import asyncio
from contextlib import asynccontextmanager

import uvicorn # type: ignore
from aerich import Command # type: ignore
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from fastadmin import fastapi_app as admin_app # type: ignore
from fastapi import FastAPI # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from tortoise.contrib.fastapi import RegisterTortoise # type: ignore

from bloobcat.bot import bot, router, setup_router
from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.admins import Admin
from bloobcat.routes import main_router, include_bot_router
from bloobcat.routes import app_info # Добавляем импорт нового роутера
from bloobcat.settings import script_settings, telegram_settings
from bloobcat.logger import get_logger

# Получаем основной логгер приложения
logger = get_logger("bloobcat")


async def setup_webhook_with_retries(webhook_url: str) -> None:
    """
    Устанавливает webhook с бесконечными попытками и экспоненциальным backoff.
    Не завершается до успешной установки webhook.
    """
    attempt = 1
    base_delay = 5  # начальная задержка 5 секунд
    max_delay = 300  # максимальная задержка 5 минут
    
    logger.info(f"🔄 Начинаю установку webhook: {webhook_url}")
    
    while True:
        try:
            # Попытка установки webhook
            await bot.set_webhook(webhook_url)
            logger.info(f"✅ Webhook успешно установлен (попытка {attempt})")
            
            # Проверяем статус webhook
            webhook_info = await bot.get_webhook_info()
            logger.info(f"📊 Статус webhook: URL={webhook_info.url}, pending_updates={webhook_info.pending_update_count}")
            
            if webhook_info.last_error_message:
                logger.warning(f"⚠️ Последняя ошибка webhook: {webhook_info.last_error_message}")
                raise Exception(f"Webhook info contains an error: {webhook_info.last_error_message}")
            
            return  # Успешно установлен, выходим
            
        except Exception as e:
            # Рассчитываем задержку с экспоненциальным backoff
            delay = min(base_delay * (2 ** min(attempt - 1, 8)), max_delay)  # ограничиваем степень до 2^8
            
            logger.warning(f"❌ Попытка {attempt} установки webhook неудачна: {e}")
            logger.info(f"⏳ Повторная попытка через {delay}с...")
            
            await asyncio.sleep(delay)
            attempt += 1
            
            # Каждые 10 попыток логируем обзорную информацию
            if attempt % 10 == 0:
                logger.info(f"📈 Статистика: выполнено {attempt} попыток установки webhook, продолжаем...")
                # Пытаемся получить текущую информацию о webhook для диагностики
                try:
                    current_webhook = await bot.get_webhook_info()
                    logger.info(f"🔍 Текущий webhook: {current_webhook.url or 'не установлен'}")
                except Exception as check_error:
                    logger.debug(f"Не удалось проверить текущий webhook: {check_error}")

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

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Личный кабинет",
            web_app=WebAppInfo(url=telegram_settings.miniapp_url)
        )
    )
    await Admin.init()
    logger.info("Инициализация бота завершена")

    webhook_url = (
        script_settings.api_url
        + "/webhook/"
        + telegram_settings.webhook_secret
    )
    
    # Запускаем установку webhook в фоновом режиме
    asyncio.create_task(setup_webhook_with_retries(webhook_url))
    
    # Запуск фоновых задач после инициализации БД и бота
    async with RegisterTortoise(
        fastapi_app,
        config=TORTOISE_ORM,
        add_exception_handlers=True,
    ):
        logger.info("Фоновые задачи запущены")
        from bloobcat.scheduler import schedule_all_tasks
        await schedule_all_tasks()
        yield
    
    # Закрытие всех клиентов RemnaWave при завершении работы
    try:
        # Удаляем webhook при остановке приложения с повторными попытками
        webhook_deleted = False
        for attempt in range(3):  # 3 попытки удаления
            try:
                await bot.delete_webhook()
                logger.info("✅ Webhook удален при остановке приложения")
                webhook_deleted = True
                break
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"⚠️ Попытка {attempt + 1} удаления webhook неудачна: {e}, повторяю...")
                    await asyncio.sleep(2)
                else:
                    logger.warning(f"❌ Не удалось удалить webhook после 3 попыток: {e}")
        
        if not webhook_deleted:
            logger.warning("🔄 Webhook не был удален, но приложение продолжает завершение")
        
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
    "https://testapp.starmy.store",
    "https://api.starmy.store",
    "https://testapi.starmy.store",
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

# Добавляем rate limiting middleware
from bloobcat.middleware.rate_limit import rate_limit_middleware

# Добавляем глобальный обработчик исключений для CORS
from fastapi import HTTPException
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    response = await http_exception_handler(request, exc)
    # Убеждаемся, что CORS заголовки добавляются даже при ошибках
    origin = request.headers.get("origin")
    if origin in origins or any(origin.endswith(o.replace("https://", "").replace("*.", "")) for o in origins if "*." in o):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Expose-Headers"] = "*"
    return response

@app.exception_handler(500)
async def internal_server_error_handler(request, exc):
    from fastapi.responses import JSONResponse
    response = JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )
    # Убеждаемся, что CORS заголовки добавляются при 500 ошибках
    origin = request.headers.get("origin")
    if origin in origins or any(origin.endswith(o.replace("https://", "").replace("*.", "")) for o in origins if "*." in o):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Expose-Headers"] = "*"
    return response

app.middleware("http")(rate_limit_middleware)

app.mount("/admin", admin_app)

# Ограничиваем права на создание/изменение/удаление: только суперюзеры имеют эти права
from fastadmin import TortoiseModelAdmin

async def only_superuser(self, user_id=None) -> bool:
    if not user_id:
        return False
    user = await Admin.filter(id=user_id, is_superuser=True).first()
    return bool(user)

# Патчим базовые методы прав доступа для всех моделей
TortoiseModelAdmin.has_add_permission = only_superuser
TortoiseModelAdmin.has_change_permission = only_superuser
TortoiseModelAdmin.has_delete_permission = only_superuser

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

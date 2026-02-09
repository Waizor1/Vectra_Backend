import asyncio
import time
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn # type: ignore
from aerich import Command # type: ignore
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from fastadmin import fastapi_app as admin_app # type: ignore
from fastapi import FastAPI, Request # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from tortoise.contrib.fastapi import RegisterTortoise # type: ignore

from bloobcat.bot import bot, router, setup_router
from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.admins import Admin
from bloobcat.routes import main_router, include_bot_router
from bloobcat.routes import app_info # Добавляем импорт нового роутера
from bloobcat.settings import script_settings, telegram_settings, test_mode
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

            # last_error_message - это ИСТОРИЧЕСКАЯ ошибка от Telegram API
            # Она сохраняется даже если webhook сейчас работает нормально
            # Проверяем только URL - если он совпадает, значит webhook установлен успешно
            if webhook_info.last_error_message:
                logger.warning(f"⚠️ Историческая ошибка webhook (можно игнорировать если webhook работает): {webhook_info.last_error_message}")

            # Webhook успешно установлен если URL совпадает
            if webhook_info.url == webhook_url:
                logger.info(f"✅ Webhook подтвержден и активен")
                return  # Успешно установлен, выходим
            else:
                # URL не совпадает - это реальная проблема
                raise Exception(f"Webhook URL не совпадает: ожидалось {webhook_url}, получено {webhook_info.url}")
            
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
        error_text = str(e).lower()
        if ("aerich" in error_text and "does not exist" in error_text) or "relation \"aerich\"" in error_text:
            logger.warning("Таблица aerich не найдена, пробую init_db для первичной инициализации")
            try:
                init_db = getattr(command, "init_db", None)
                if init_db is None:
                    raise RuntimeError("В Aerich отсутствует метод init_db")
                await init_db(safe=True)
                logger.info("Первичная инициализация БД завершена, повторяю миграции")
                await command.upgrade(run_in_transaction=True)
                logger.info("Применение миграций завершено")
            except Exception as init_db_error:
                logger.error(
                    f"Ошибка при первичной инициализации БД: {init_db_error}",
                    exc_info=True
                )
                raise
        else:
            logger.error(f"Ошибка при инициализации базы данных: {str(e)}", exc_info=True)
            raise

    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Личный кабинет",
                web_app=WebAppInfo(url=telegram_settings.miniapp_url)
            )
        )
    except Exception as e:
        logger.error(f"Не удалось установить кнопку меню Telegram: {e}", exc_info=True)
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
        if test_mode:
            try:
                from bloobcat.testdata import seed_test_fixtures

                await seed_test_fixtures()
            except Exception as e:
                logger.error(f"Не удалось подготовить тестовые данные (TESTMODE): {e}", exc_info=True)
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

# Healthcheck endpoint - должен быть ПЕРЕД middleware для быстрой проверки
@app.get("/health")
async def health_check():
    """
    Простой healthcheck endpoint для мониторинга состояния приложения.
    Возвращает текущий статус и timestamp.
    """
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "bloobcat"
    }

# Middleware для мониторинга долгих запросов
@app.middleware("http")
async def monitor_slow_requests(request: Request, call_next):
    """
    Мониторинг времени выполнения запросов.
    Логирует предупреждения для запросов > 5 сек и ошибки для запросов > 30 сек.
    Исключает /health из мониторинга для предотвращения засорения логов.
    """
    # Пропускаем мониторинг для healthcheck endpoint
    if request.url.path == "/health":
        return await call_next(request)

    start_time = time.time()

    try:
        response = await call_next(request)
    except Exception as e:
        duration = time.time() - start_time
        logger.error(
            f"Ошибка при обработке запроса: {request.method} {request.url.path} "
            f"(заняло {duration:.2f} сек): {e}",
            extra={
                'method': request.method,
                'path': request.url.path,
                'duration': duration,
                'error': str(e)
            }
        )
        raise

    duration = time.time() - start_time

    # Логируем медленные запросы (> 5 секунд)
    if duration > 5.0:
        logger.warning(
            f"Медленный запрос: {request.method} {request.url.path} "
            f"занял {duration:.2f} сек",
            extra={
                'method': request.method,
                'path': request.url.path,
                'duration': duration
            }
        )

    # Логируем критически долгие запросы (> 30 секунд)
    if duration > 30.0:
        logger.error(
            f"КРИТИЧЕСКИ медленный запрос: {request.method} {request.url.path} "
            f"занял {duration:.2f} сек",
            extra={
                'method': request.method,
                'path': request.url.path,
                'duration': duration
            }
        )

    return response

# Cache headers for stable GET endpoints
PUBLIC_CACHE_PATHS = {"/pay/tariffs", "/app/info", "/subscription/plans"}
NO_STORE_PREFIXES = ("/user", "/devices", "/family", "/partner", "/subscription/status")

@app.middleware("http")
async def cache_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.method != "GET":
        return response

    path = request.url.path
    if path in PUBLIC_CACHE_PATHS:
        response.headers["Cache-Control"] = "public, max-age=300"
        try:
            body = response.body or b""
            etag = hashlib.md5(body).hexdigest()
            response.headers["ETag"] = etag
            if request.headers.get("if-none-match") == etag:
                response.status_code = 304
                response.body = b""
                response.headers.pop("content-length", None)
        except Exception:
            pass
        return response

    if path.startswith(NO_STORE_PREFIXES):
        response.headers["Cache-Control"] = "no-store"
    return response

origins = [
    "https://ttestapp.guarddogvpn.com",
    "https://app.guarddogvpn.com", 
    "https://app.starmy.store",
    "https://testapp.starmy.store",
    "https://api.starmy.store",
    "https://testapi.starmy.store",
    "https://v3018884.hosted-by-vdsina.ru",
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
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    response = await http_exception_handler(request, exc)
    # Убеждаемся, что CORS заголовки добавляются даже при ошибках
    origin = request.headers.get("origin")
    if origin and (origin in origins or any(origin.endswith(o.replace("https://", "").replace("*.", "")) for o in origins if "*." in o)):
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
    if origin and (origin in origins or any(origin.endswith(o.replace("https://", "").replace("*.", "")) for o in origins if "*." in o)):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Expose-Headers"] = "*"
    return response

# Гарантируем CORS-заголовки и для FastAPI HTTPException (включая 429)
@app.exception_handler(FastAPIHTTPException)
async def custom_fastapi_http_exception_handler(request, exc: FastAPIHTTPException):
    from fastapi.responses import JSONResponse
    response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    origin = request.headers.get("origin")
    if origin and (origin in origins or any(origin.endswith(o.replace("https://", "").replace("*.", "")) for o in origins if "*." in o)):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Expose-Headers"] = "*"
    # Пробрасываем служебные заголовки (например, Retry-After)
    if exc.headers:
        for k, v in exc.headers.items():
            response.headers[k] = v
    return response

app.middleware("http")(rate_limit_middleware)


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

# Монтируем FastAdmin после API-роутеров, чтобы не перекрывать /admin/integration
app.mount("/admin", admin_app)


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

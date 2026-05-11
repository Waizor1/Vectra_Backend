import asyncio
import importlib.util
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn  # type: ignore
from aerich import Command  # type: ignore
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from fastapi import FastAPI, Request  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from tortoise import Tortoise
from tortoise.contrib.fastapi import RegisterTortoise  # type: ignore

from bloobcat.bot import bot, router, setup_router
from bloobcat.build_info import get_build_info
from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.admins import Admin
from bloobcat.db.fk_guards import (
    ensure_active_tariffs_fk_cascade,
    ensure_notification_marks_fk_cascade,
    ensure_promo_usages_fk_cascade,
    ensure_users_referred_by_fk_set_null,
)
from bloobcat.routes import main_router, include_bot_router
from bloobcat.routes import app_info  # Добавляем импорт нового роутера
from bloobcat.settings import (
    cors_settings,
    script_settings,
    telegram_settings,
    test_mode,
)
from bloobcat.logger import get_logger
from bloobcat.utils.cors import (
    add_cors_error_headers,
    resolve_runtime_cors_policy,
)

# Получаем основной логгер приложения
logger = get_logger("bloobcat")

_build_info = get_build_info()
logger.info(
    "Build info: version={} build_time={}",
    _build_info["version"],
    _build_info["build_time"],
)

RUNTIME_CORS_ALLOWED_ORIGINS, RUNTIME_CORS_ALLOW_ORIGIN_REGEX = (
    resolve_runtime_cors_policy(
        cors_settings.allow_origins,
        cors_settings.allow_origin_regex,
        cors_settings.strict_allowlist,
        cors_settings.allow_loopback_http,
    )
)
logger.info(
    "CORS runtime policy: strict_allowlist={} origins_count={} origins={} regex_enabled={}",
    cors_settings.strict_allowlist,
    len(RUNTIME_CORS_ALLOWED_ORIGINS),
    RUNTIME_CORS_ALLOWED_ORIGINS,
    RUNTIME_CORS_ALLOW_ORIGIN_REGEX is not None,
)


def _is_fk_guard_startup_strict() -> bool:
    """Default strict mode for FK startup guards (fail-closed)."""
    value = os.getenv("FK_GUARD_STARTUP_STRICT", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _is_generate_schema_startup_enabled() -> bool:
    """Clean staging/dev escape hatch when Aerich migration files are incompatible."""
    value = os.getenv("SCHEMA_INIT_GENERATE_ONLY", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


async def _schema_already_initialized(conn) -> bool:
    rows = await conn.execute_query_dict(
        "SELECT to_regclass('public.users') AS users_table, "
        "to_regclass('public.auth_identities') AS auth_table"
    )
    if not rows:
        return False
    row = rows[0]
    return bool(row.get("users_table") and row.get("auth_table"))


async def _apply_legacy_sql_migrations(conn) -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "models"
    migration_files = sorted(migrations_dir.glob("*.py"), key=_migration_sort_key)
    logger.warning(
        "Applying legacy SQL migrations from {} files in {}",
        len(migration_files),
        migrations_dir,
    )
    for migration_file in migration_files:
        module_name = f"_vectra_stage_migration_{migration_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, migration_file)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load migration {migration_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        upgrade = getattr(module, "upgrade", None)
        if upgrade is None:
            continue
        sql = await upgrade(conn)
        if str(sql or "").strip():
            logger.info("Applying legacy migration {}", migration_file.name)
            await conn.execute_script(sql)


async def _apply_generate_schema_compat_patches(conn) -> None:
    """
    Apply idempotent runtime patches needed before the app can reach deploy
    migration verification when SCHEMA_INIT_GENERATE_ONLY is enabled.

    In that mode, existing databases intentionally skip Aerich startup
    migrations and the deploy workflow runs explicit migrations only after the
    service becomes healthy. New model fields that are queried during startup
    therefore need a tiny safe bootstrap here.
    """
    await conn.execute_script(
        """
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "email_notifications_enabled"
            BOOLEAN NOT NULL DEFAULT TRUE;
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "partner_link_mode"
            VARCHAR(8) NOT NULL DEFAULT 'bot';
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "key_activated"
            BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "trial_started_at" TIMESTAMPTZ;
        UPDATE "users"
        SET "trial_started_at" = COALESCE("registration_date", "created_at", NOW())
        WHERE "trial_started_at" IS NULL
          AND "used_trial" = TRUE;

        CREATE TABLE IF NOT EXISTS "user_devices" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "family_member_id" UUID REFERENCES "family_members" ("id") ON DELETE CASCADE,
            "kind" VARCHAR(16) NOT NULL,
            "remnawave_uuid" UUID,
            "hwid" VARCHAR(255),
            "device_name" VARCHAR(128),
            "platform" VARCHAR(64),
            "device_model" VARCHAR(128),
            "os_version" VARCHAR(64),
            "metadata" JSONB,
            "meta_refreshed_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "last_online_at" TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS "idx_user_devices_user_id"
            ON "user_devices" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_user_devices_family_member_id"
            ON "user_devices" ("family_member_id");
        CREATE INDEX IF NOT EXISTS "idx_user_devices_remnawave_uuid"
            ON "user_devices" ("remnawave_uuid");
        CREATE INDEX IF NOT EXISTS "idx_user_devices_hwid"
            ON "user_devices" ("hwid");

        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "device_per_user_enabled" BOOLEAN;
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "temp_setup_token" VARCHAR(255);
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "temp_setup_expires_at" TIMESTAMPTZ;
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "temp_setup_device_id" INT;
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_users_temp_setup_token"
            ON "users" ("temp_setup_token");
        ALTER TABLE IF EXISTS "users"
            ADD COLUMN IF NOT EXISTS "admin_lte_granted_at" TIMESTAMPTZ;
        """
    )


def _migration_sort_key(migration_file: Path) -> tuple[int, str]:
    """Sort Aerich legacy migrations by numeric prefix, not lexicographically."""
    prefix = migration_file.name.split("_", 1)[0]
    if prefix.isdigit():
        return (int(prefix), migration_file.name)
    return (10**9, migration_file.name)


async def _initialize_schema_without_aerich() -> None:
    logger.warning(
        "SCHEMA_INIT_GENERATE_ONLY=true: создаю схему без Aerich runtime migrations"
    )
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        conn = Tortoise.get_connection("default")
        if await _schema_already_initialized(conn):
            await _apply_generate_schema_compat_patches(conn)
            logger.info(
                "Schema already initialized; applied generate-schema compatibility patches"
            )
            return
        try:
            await Tortoise.generate_schemas(safe=True)
            logger.info("Schema generated from current Tortoise models")
            await _apply_generate_schema_compat_patches(conn)
        except Exception as schema_error:
            logger.warning(
                "Tortoise schema generation failed ({}), falling back to legacy SQL migrations",
                schema_error,
            )
            await _apply_legacy_sql_migrations(conn)
            await _apply_generate_schema_compat_patches(conn)
    finally:
        await Tortoise.close_connections()


def _redact_webhook_url(raw_url: str | None) -> str:
    if not raw_url:
        return "не установлен"
    if "/webhook/" not in raw_url:
        return raw_url
    return raw_url.split("/webhook/", 1)[0] + "/webhook/[redacted]"


async def setup_webhook_with_retries(webhook_url: str) -> None:
    """
    Устанавливает webhook с бесконечными попытками и экспоненциальным backoff.
    Не завершается до успешной установки webhook.
    """
    attempt = 1
    base_delay = 5  # начальная задержка 5 секунд
    max_delay = 300  # максимальная задержка 5 минут

    safe_webhook_url = _redact_webhook_url(webhook_url)
    logger.info(f"🔄 Начинаю установку webhook: {safe_webhook_url}")

    while True:
        try:
            # Попытка установки webhook
            await bot.set_webhook(webhook_url)
            logger.info(f"✅ Webhook успешно установлен (попытка {attempt})")

            # Проверяем статус webhook
            webhook_info = await bot.get_webhook_info()
            logger.info(
                "📊 Статус webhook: URL={}, pending_updates={}",
                _redact_webhook_url(webhook_info.url),
                webhook_info.pending_update_count,
            )

            # last_error_message - это ИСТОРИЧЕСКАЯ ошибка от Telegram API
            # Она сохраняется даже если webhook сейчас работает нормально
            # Проверяем только URL - если он совпадает, значит webhook установлен успешно
            if webhook_info.last_error_message:
                logger.warning(
                    f"⚠️ Историческая ошибка webhook (можно игнорировать если webhook работает): {webhook_info.last_error_message}"
                )

            # Webhook успешно установлен если URL совпадает
            if webhook_info.url == webhook_url:
                logger.info(f"✅ Webhook подтвержден и активен")
                return  # Успешно установлен, выходим
            else:
                # URL не совпадает - это реальная проблема
                raise Exception(
                    "Webhook URL не совпадает: ожидалось "
                    f"{safe_webhook_url}, получено {_redact_webhook_url(webhook_info.url)}"
                )

        except Exception as e:
            # Рассчитываем задержку с экспоненциальным backoff
            delay = min(
                base_delay * (2 ** min(attempt - 1, 8)), max_delay
            )  # ограничиваем степень до 2^8

            logger.warning(f"❌ Попытка {attempt} установки webhook неудачна: {e}")
            logger.info(f"⏳ Повторная попытка через {delay}с...")

            await asyncio.sleep(delay)
            attempt += 1

            # Каждые 10 попыток логируем обзорную информацию
            if attempt % 10 == 0:
                logger.info(
                    f"📈 Статистика: выполнено {attempt} попыток установки webhook, продолжаем..."
                )
                # Пытаемся получить текущую информацию о webhook для диагностики
                try:
                    current_webhook = await bot.get_webhook_info()
                    logger.info(
                        "🔍 Текущий webhook: {}",
                        _redact_webhook_url(current_webhook.url),
                    )
                except Exception as check_error:
                    logger.debug(f"Не удалось проверить текущий webhook: {check_error}")


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    command = Command(tortoise_config=TORTOISE_ORM, location="migrations")
    # Небольшая задержка перед инициализацией миграций
    await asyncio.sleep(5)

    if _is_generate_schema_startup_enabled():
        await _initialize_schema_without_aerich()
    else:
        try:
            logger.info("Инициализация базы данных...")
            await command.init()

            logger.info("Применение миграций...")
            await command.upgrade(run_in_transaction=True)
            logger.info("Применение миграций завершено")
        except Exception as e:
            error_text = str(e).lower()
            if (
                "aerich" in error_text and "does not exist" in error_text
            ) or 'relation "aerich"' in error_text:
                logger.warning(
                    "Таблица aerich не найдена, пробую init_db для первичной инициализации"
                )
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
                        exc_info=True,
                    )
                    raise
            else:
                logger.error(
                    f"Ошибка при инициализации базы данных: {str(e)}", exc_info=True
                )
                raise

    # Aerich может закрыть соединения после upgrade, поэтому перед self-heal
    # поднимаем временное подключение Tortoise при необходимости.
    guard_connection_initialized = False
    try:
        try:
            Tortoise.get_connection("default")
        except Exception:
            logger.info(
                "Инициализация временного Tortoise connection перед FK self-heal guard"
            )
            await Tortoise.init(config=TORTOISE_ORM)
            guard_connection_initialized = True

        ok_at = await ensure_active_tariffs_fk_cascade()
        ok_nm = await ensure_notification_marks_fk_cascade()
        ok_pu = await ensure_promo_usages_fk_cascade()
        ok_users_ref = await ensure_users_referred_by_fk_set_null()
        if ok_at and ok_nm and ok_pu and ok_users_ref:
            logger.info("FK self-heal guard выполнен")
        else:
            strict_fk_startup = _is_fk_guard_startup_strict()
            logger_message = (
                "FK self-heal guard завершен с предупреждениями: "
                "active_tariffs={}, notification_marks={}, promo_usages={}, users_referred_by={}, strict={}"
            )
            if strict_fk_startup:
                logger.error(
                    logger_message,
                    ok_at,
                    ok_nm,
                    ok_pu,
                    ok_users_ref,
                    strict_fk_startup,
                )
                raise RuntimeError("FK self-heal guard failed in strict startup mode")
            logger.warning(
                logger_message,
                ok_at,
                ok_nm,
                ok_pu,
                ok_users_ref,
                strict_fk_startup,
            )
    finally:
        if guard_connection_initialized:
            await Tortoise.close_connections()
            logger.info("Временное Tortoise connection после FK self-heal закрыто")

    if telegram_settings.webhook_enabled:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Личный кабинет",
                    web_app=WebAppInfo(url=telegram_settings.miniapp_url),
                )
            )
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Open Vectra Connect"),
                    BotCommand(command="documents", description="Documents and support"),
                ]
            )
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Открыть Vectra Connect"),
                    BotCommand(command="documents", description="Документы и поддержка"),
                ],
                language_code="ru",
            )
        except Exception as e:
            logger.error(f"Не удалось установить кнопку меню Telegram: {e}", exc_info=True)
    else:
        logger.info("Telegram webhook/menu setup disabled by TELEGRAM_WEBHOOK_ENABLED=false")
    await Admin.init()
    logger.info("Инициализация бота завершена")

    if telegram_settings.webhook_enabled:
        webhook_url = (
            script_settings.api_url + "/webhook/" + telegram_settings.webhook_secret
        )

        # Запускаем установку webhook в фоновом режиме
        asyncio.create_task(setup_webhook_with_retries(webhook_url))
    else:
        logger.info("Telegram webhook registration skipped by TELEGRAM_WEBHOOK_ENABLED=false")

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
                logger.error(
                    f"Не удалось подготовить тестовые данные (TESTMODE): {e}",
                    exc_info=True,
                )
        await schedule_all_tasks()
        yield

    # Закрытие всех клиентов RemnaWave при завершении работы
    try:
        if telegram_settings.webhook_enabled and telegram_settings.delete_webhook_on_shutdown:
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
                        logger.warning(
                            f"⚠️ Попытка {attempt + 1} удаления webhook неудачна: {e}, повторяю..."
                        )
                        await asyncio.sleep(2)
                    else:
                        logger.warning(
                            f"❌ Не удалось удалить webhook после 3 попыток: {e}"
                        )

            if not webhook_deleted:
                logger.warning(
                    "🔄 Webhook не был удален, но приложение продолжает завершение"
                )
        elif telegram_settings.webhook_enabled:
            logger.info("Telegram webhook deletion skipped by TELEGRAM_DELETE_WEBHOOK_ON_SHUTDOWN=false")
        else:
            logger.info("Telegram webhook deletion skipped by TELEGRAM_WEBHOOK_ENABLED=false")

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

            if "bloobcat.processing.remnawave_processor" in sys.modules:
                remnawave_module = sys.modules.get(
                    "bloobcat.processing.remnawave_processor"
                )
                if (
                    hasattr(remnawave_module, "remnawave_client_instance")
                    and remnawave_module.remnawave_client_instance
                ):
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
        "service": "bloobcat",
        **get_build_info(),
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
                "method": request.method,
                "path": request.url.path,
                "duration": duration,
                "error": str(e),
            },
        )
        raise

    duration = time.time() - start_time

    # Логируем медленные запросы (> 5 секунд)
    if duration > 5.0:
        logger.warning(
            f"Медленный запрос: {request.method} {request.url.path} "
            f"занял {duration:.2f} сек",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration": duration,
            },
        )

    # Логируем критически долгие запросы (> 30 секунд)
    if duration > 30.0:
        logger.error(
            f"КРИТИЧЕСКИ медленный запрос: {request.method} {request.url.path} "
            f"занял {duration:.2f} сек",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration": duration,
            },
        )

    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=RUNTIME_CORS_ALLOWED_ORIGINS,  # разрешаем только конкретные домены
    allow_credentials=True,  # включаем учетные данные
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,  # кэширование preflight запросов на 24 часа
    allow_origin_regex=RUNTIME_CORS_ALLOW_ORIGIN_REGEX,
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
    add_cors_error_headers(
        response,
        origin,
        RUNTIME_CORS_ALLOWED_ORIGINS,
        RUNTIME_CORS_ALLOW_ORIGIN_REGEX,
        cors_settings.allow_loopback_http,
    )
    return response


@app.exception_handler(500)
async def internal_server_error_handler(request, exc):
    from fastapi.responses import JSONResponse

    response = JSONResponse(
        status_code=500, content={"detail": "Internal server error"}
    )
    # Убеждаемся, что CORS заголовки добавляются при 500 ошибках
    origin = request.headers.get("origin")
    add_cors_error_headers(
        response,
        origin,
        RUNTIME_CORS_ALLOWED_ORIGINS,
        RUNTIME_CORS_ALLOW_ORIGIN_REGEX,
        cors_settings.allow_loopback_http,
    )
    return response


# Гарантируем CORS-заголовки и для FastAPI HTTPException (включая 429)
@app.exception_handler(FastAPIHTTPException)
async def custom_fastapi_http_exception_handler(request, exc: FastAPIHTTPException):
    from fastapi.responses import JSONResponse

    response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    origin = request.headers.get("origin")
    add_cors_error_headers(
        response,
        origin,
        RUNTIME_CORS_ALLOWED_ORIGINS,
        RUNTIME_CORS_ALLOW_ORIGIN_REGEX,
        cors_settings.allow_loopback_http,
    )
    # Пробрасываем служебные заголовки (например, Retry-After)
    if exc.headers:
        for k, v in exc.headers.items():
            response.headers[k] = v
    return response


app.middleware("http")(rate_limit_middleware)


setup_router()
include_bot_router()
app.include_router(router)
app.include_router(main_router)
app.include_router(app_info.router)  # Регистрируем новый роутер

# FastAdmin удалён: административный UI полностью переехал в Directus.
# Наши собственные API-роутеры (включая /admin/integration) остаются
# доступными как обычные FastAPI-эндпоинты выше.


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
        logger.info("Завершение работы скрипта.")

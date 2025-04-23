from fastapi import APIRouter
from datetime import datetime, timedelta
import asyncio
from typing import Dict, Any, Optional

from bloobcat.bot.notifications.admin import on_activated_key
from bloobcat.db.users import Users
from bloobcat.db.connections import Connections
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from .client import RemnaWaveClient
from bloobcat.settings import remnawave_settings
from bloobcat.processing.user_processor import process_user_safe, process_user

router = APIRouter(prefix="/remnawave", tags=["remnawave"])
logger = get_logger("remnawave_catcher")

remnawave = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())

last_activated_info = {}
last_notification_expired = {}
user_state_cache: Dict[str, Dict[str, Any]] = {}
update_in_progress = False

@router.get("/webhook")
async def webhook():
    """Обработчик вебхука для запуска обновления RemnaWave"""
    try:
        logger.info("Получен запрос на вебхук, запуск remnawave_updater")
        await remnawave_updater()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка в обработчике вебхука: {str(e)}")
        return {"status": "error", "detail": str(e)}

@router.get("/fast_update/{user_id}")
async def fast_update(user_id: int):
    """Быстрое обновление для конкретного пользователя"""
    try:
        logger.info(f"Получен запрос на быстрое обновление для пользователя {user_id}")
        user = await Users.get_or_none(id=user_id)
        if not user:
            return {"status": "error", "detail": "Пользователь не найден"}
        
        try:
            if user.remnawave_uuid:
                remnawave_user = await remnawave.users.get_user_by_uuid(user.remnawave_uuid)
                remnawave_users_dict = {user.remnawave_uuid: remnawave_user["response"]}
                logger.info(f"Получены данные о пользователе {user_id} из RemnaWave")
            else:
                logger.info(f"У пользователя {user_id} нет UUID RemnaWave")
                remnawave_users_dict = {}
            
            await process_user(user, remnawave_users_dict)
        except Exception as e:
            logger.error(f"Ошибка при получении данных из RemnaWave: {str(e)}")
            await process_user(user)
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка в обработчике быстрого обновления для пользователя {user_id}: {str(e)}")
        return {"status": "error", "detail": str(e)}

async def remnawave_updater():
    """Основной процесс обновления данных RemnaWave"""
    global update_in_progress
    
    if update_in_progress:
        logger.info("Процесс обновления уже запущен, пропускаем")
        return
    
    update_in_progress = True
    start_time = datetime.now()
    logger.info(f"Запуск remnawave_updater в {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    success = 0
    errors = 0
    registered = 0
    
    try:
        max_retry_time = 30
        retry_interval = 3
        start_retry_time = datetime.now()
        
        users = None
        retry_attempt = 0
        
        while users is None:
            try:
                users = await Users.all()
                logger.info(f"Получено {len(users)} пользователей для обработки")
            except Exception as e:
                retry_attempt += 1
                elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                
                if elapsed_retry_time > max_retry_time:
                    logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек). Последняя ошибка: {str(e)}")
                    raise
                
                logger.warning(f"Ошибка при получении пользователей (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)
        
        if users is None:
            logger.error("Не удалось получить пользователей после всех попыток")
            return
        
        try:
            logger.info("Начинаем получение списка пользователей из RemnaWave")
            remnawave_users_response = await remnawave.users.get_users(size=100)
            remnawave_users = remnawave_users_response.get("response", {}).get("users", [])
            total_users = remnawave_users_response.get("response", {}).get("total", 0)
            logger.info(f"Получено {len(remnawave_users)} пользователей из RemnaWave (всего: {total_users})")
            
            remnawave_users_dict = {user['uuid']: user for user in remnawave_users}
            logger.info(f"Словарь пользователей RemnaWave создан с {len(remnawave_users_dict)} записями")
        except Exception as e:
            logger.error(f"Ошибка при получении списка пользователей из RemnaWave: {str(e)}")
            logger.warning("Продолжаем работу без предварительно загруженного списка пользователей RemnaWave")
            remnawave_users_dict = {}
        
        priority_users = [u for u in users if u.connected_at and not u.is_registered]
        if priority_users:
            logger.info(f"Найдено {len(priority_users)} приоритетных пользователей")
            sem = asyncio.Semaphore(10)
            
            async def process_with_semaphore(user):
                async with sem:
                    return await process_user_safe(user, remnawave_users_dict)
            
            priority_results = await asyncio.gather(
                *[process_with_semaphore(user) for user in priority_users], 
                return_exceptions=True
            )
            
            for result in priority_results:
                if result is True:
                    success += 1
                    registered += 1
                elif result is False:
                    errors += 1
        
        regular_users = [u for u in users if u not in priority_users]
        if regular_users:
            batch_size = 20
            for i in range(0, len(regular_users), batch_size):
                batch = regular_users[i:i+batch_size]
                logger.info(f"Обработка пакета пользователей {i+1}-{i+len(batch)} из {len(regular_users)}")
                
                batch_results = await asyncio.gather(
                    *[process_user_safe(user, remnawave_users_dict) for user in batch], 
                    return_exceptions=True
                )
                
                for result in batch_results:
                    if result is True:
                        success += 1
                    elif result is False:
                        errors += 1
                
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Обработка завершена за {elapsed:.2f} секунд. Успешно: {success}, зарегистрировано: {registered}, ошибок: {errors}")
    except Exception as e:
        logger.error(f"Критическая ошибка в remnawave_updater: {str(e)}")
    finally:
        update_in_progress = False
        logger.info(f"Завершение работы remnawave_updater, время выполнения: {(datetime.now() - start_time).total_seconds():.2f} секунд") 
from fastapi import APIRouter
from datetime import date, datetime, timedelta
import asyncio
from typing import List, Dict, Any, Optional

from bloobcat.bot.notifications.admin import on_activated_key
from bloobcat.db.users import Users
from bloobcat.db.connections import Connections
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from .client import MarzbanClient
from bloobcat.processing.user_processor import process_user_safe, process_user

router = APIRouter(prefix="/marzban", tags=["marzban"])
logger = get_logger("marzban_catcher")

marzban = MarzbanClient()

last_activated_info = {}
last_notification_expired = {}
# Кеш состояния пользователей для уменьшения числа запросов
user_state_cache: Dict[int, Dict[str, Any]] = {}
# Флаг запущенного процесса обновления
update_in_progress = False


@router.get("/webhook")
async def webhook():
    """Обработчик вебхука для запуска обновления Marzban"""
    try:
        logger.info("Получен запрос на вебхук, запуск marzban_updater")
        await marzban_updater()
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
        
        # Получаем данные о пользователе из Marzban
        try:
            # Оптимизируем запрос - используем метод get_users с фильтрацией по username
            # Это более эффективно, так как API может фильтровать на своей стороне
            logger.info(f"Запрашиваем данные о пользователе {user_id} из Marzban через фильтр")
            users_response = await marzban.users.get_users(
                username=str(user_id),  # Фильтруем по username равному user_id
                limit=1,                # Нам нужен только один пользователь
                get_all=False           # Не нужно загружать все страницы
            )
            
            users = users_response.get("users", [])
            if users and len(users) > 0:
                marzban_user = users[0]
                marzban_users_dict = {str(user_id): marzban_user}
                logger.info(f"Получены данные о пользователе {user_id} из Marzban через фильтр")
            else:
                logger.info(f"Пользователь {user_id} не найден в Marzban при фильтрации, создаем пустой словарь")
                marzban_users_dict = {}
            
            # Обрабатываем пользователя с использованием полученных данных
            await process_user(user, marzban_users_dict)
        except Exception as e:
            logger.error(f"Ошибка при получении данных из Marzban: {str(e)}")
            # Запасной вариант - обрабатываем пользователя без предварительно полученных данных
            await process_user(user)
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка в обработчике быстрого обновления для пользователя {user_id}: {str(e)}")
        return {"status": "error", "detail": str(e)}


async def marzban_updater():
    """Основной процесс обновления данных Marzban"""
    global update_in_progress
    
    # Проверяем, не запущен ли уже процесс обновления
    if update_in_progress:
        logger.info("Процесс обновления уже запущен, пропускаем")
        return
    
    update_in_progress = True
    start_time = datetime.now()
    logger.info(f"Запуск marzban_updater в {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    success = 0
    errors = 0
    registered = 0
    
    try:
        # Добавляем механизм повторных попыток при ошибке подключения к базе данных
        max_retry_time = 30  # максимальное время повторных попыток в секундах
        retry_interval = 3   # интервал между повторными попытками в секундах
        start_retry_time = datetime.now()
        
        users = None
        retry_attempt = 0
        
        # Пытаемся получить пользователей с повторными попытками
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
                
                logger.warning(f"Ошибка при получении пользователей (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек. Прошло {elapsed_retry_time:.1f} из {max_retry_time} сек.")
                await asyncio.sleep(retry_interval)
        
        # Если users все еще None после всех попыток
        if users is None:
            logger.error("Не удалось получить пользователей после всех попыток")
            return
        
        # Получаем всех пользователей из Marzban за один запрос
        try:
            logger.info("Начинаем получение списка пользователей из Marzban с пагинацией")
            marzban_users_response = await marzban.users.get_users(limit=100, max_retries=3, retry_delay=3)
            marzban_users = marzban_users_response.get("users", [])
            total_users = marzban_users_response.get("total", 0)
            logger.info(f"Получено {len(marzban_users)} пользователей из Marzban (всего в системе: {total_users})")
            
            # Создаем словарь для быстрого поиска пользователей Marzban по username
            marzban_users_dict = {user['username']: user for user in marzban_users}
            logger.info(f"Словарь пользователей Marzban успешно создан с {len(marzban_users_dict)} записями")
        except Exception as e:
            logger.error(f"Ошибка при получении списка пользователей из Marzban: {str(e)}")
            logger.warning("Продолжаем работу без предварительно загруженного списка пользователей Marzban")
            marzban_users_dict = {}
        
        # Сначала обрабатываем пользователей с connected_at и без is_registered (новые)
        priority_users = [u for u in users if u.connected_at and not u.is_registered]
        if priority_users:
            logger.info(f"Найдено {len(priority_users)} приоритетных пользователей")
            # Используем gather с semaphore для контроля конкурентности
            sem = asyncio.Semaphore(10)  # Ограничиваем до 10 одновременных задач
            
            async def process_with_semaphore(user):
                async with sem:
                    return await process_user_safe(user, marzban_users_dict)
            
            # Запускаем обработку приоритетных пользователей
            priority_results = await asyncio.gather(
                *[process_with_semaphore(user) for user in priority_users], 
                return_exceptions=True
            )
            
            # Подсчитываем результаты
            for result in priority_results:
                if result is True:
                    success += 1
                    registered += 1
                elif result is False:
                    errors += 1
        
        # Обрабатываем остальных пользователей с меньшим приоритетом
        regular_users = [u for u in users if u not in priority_users]
        if regular_users:
            # Разбиваем обработку на пакеты по 20 пользователей
            batch_size = 20
            for i in range(0, len(regular_users), batch_size):
                batch = regular_users[i:i+batch_size]
                logger.info(f"Обработка пакета пользователей {i+1}-{i+len(batch)} из {len(regular_users)}")
                
                # Запускаем обработку пакета пользователей
                batch_results = await asyncio.gather(
                    *[process_user_safe(user, marzban_users_dict) for user in batch], 
                    return_exceptions=True
                )
                
                # Подсчитываем результаты
                for result in batch_results:
                    if result is True:
                        success += 1
                    elif result is False:
                        errors += 1
                
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"Обработка завершена за {elapsed:.2f} секунд. Успешно: {success}, зарегистрировано: {registered}, ошибок: {errors}")
    except Exception as e:
        logger.error(f"Критическая ошибка в marzban_updater: {str(e)}")
    finally:
        update_in_progress = False
        logger.info(f"Завершение работы marzban_updater, время выполнения: {(datetime.now() - start_time).total_seconds():.2f} секунд")

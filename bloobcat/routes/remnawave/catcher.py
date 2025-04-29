from fastapi import APIRouter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncio
from typing import Dict, Any, Optional

from bloobcat.bot.notifications.admin import on_activated_key
from bloobcat.db.users import Users
from bloobcat.db.connections import Connections
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from .client import RemnaWaveClient
from bloobcat.settings import remnawave_settings

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


async def remnawave_updater():
    """Основной процесс синхронизации дат истечения из БД в RemnaWave"""
    global update_in_progress
    
    if update_in_progress:
        logger.info("Процесс обновления уже запущен, пропускаем")
        return
    
    update_in_progress = True
    start_time = datetime.now(ZoneInfo("Europe/Moscow"))
    logger.info(f"Запуск remnawave_updater в {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    updated = 0
    errors = 0
    
    # Константы для повторных попыток
    max_retry_time = 30  # максимальное время для повторных попыток в секундах
    retry_interval = 3   # интервал между попытками в секундах
    
    try:
        # Проверка соединения с API с повторными попытками
        remnawave_nodes = None
        start_retry_time = datetime.now()
        retry_attempt = 0
        
        while remnawave_nodes is None:
            try:
                retry_attempt += 1
                remnawave_nodes = await remnawave.nodes.get_nodes()
            except Exception as e:
                elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                
                if elapsed_retry_time > max_retry_time:
                    logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек) для API. Последняя ошибка: {str(e)}")
                    return
                
                logger.warning(f"Ошибка при проверке соединения с API (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)
        
        # Проверка соединения с БД с повторными попытками
        users = None
        start_retry_time = datetime.now()
        retry_attempt = 0
        
        while users is None:
            try:
                retry_attempt += 1
                users = await Users.all()
                if users is None:
                    raise Exception("Результат запроса users is None")
                logger.debug(f"Получено {len(users)} пользователей из БД")
            except Exception as e:
                elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                
                if elapsed_retry_time > max_retry_time:
                    logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек) для БД. Последняя ошибка: {str(e)}")
                    return
                
                logger.warning(f"Ошибка при получении пользователей из БД (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)
            
        # Проверка пользователей    
        users_with_uuid = [u for u in users if u.remnawave_uuid and hasattr(u, 'expired_at') and u.expired_at]
        if not users_with_uuid:
            logger.warning("Не найдено пользователей с UUID и датой истечения")
            return
            
        logger.debug(f"Найдено {len(users_with_uuid)} пользователей с UUID и датой истечения")
        
        # Получаем данные из RemnaWave с повторными попытками
        remnawave_users = []
        start_retry_time = datetime.now()
        retry_attempt = 0
        
        # Размер страницы и начальное смещение
        page_size = 100
        start_index = 0
        total_users = None
        
        while total_users is None or start_index < total_users:
            try:
                retry_attempt += 1
                logger.debug(f"Получение списка пользователей из RemnaWave (страница {start_index//page_size + 1})")
                
                remnawave_users_response = await remnawave.users.get_users(size=page_size, start=start_index)
                
                if not remnawave_users_response:
                    raise Exception("Пустой ответ от RemnaWave API при получении пользователей")
                    
                if "response" not in remnawave_users_response:
                    raise Exception(f"Некорректный ответ от RemnaWave API: {remnawave_users_response}")
                    
                if "users" not in remnawave_users_response["response"]:
                    raise Exception(f"В ответе от RemnaWave API отсутствует поле users: {remnawave_users_response}")
                
                # Обновляем общее количество пользователей если не было известно
                if total_users is None and "total" in remnawave_users_response["response"]:
                    total_users = remnawave_users_response["response"]["total"]
                    logger.debug(f"Всего пользователей в RemnaWave: {total_users}")
                    
                page_users = remnawave_users_response["response"]["users"]
                if page_users is None:
                    raise Exception("Поле users в ответе RemnaWave API равно None")
                
                # Добавляем пользователей текущей страницы в общий список
                remnawave_users.extend(page_users)
                logger.debug(f"Получено {len(page_users)} пользователей на странице {start_index//page_size + 1}")
                
                # Если страница пустая или мы получили меньше, чем размер страницы - прерываем
                if not page_users or len(page_users) < page_size:
                    break
                    
                # Увеличиваем смещение для следующей страницы
                start_index += page_size
                
                # Если мы получили все, прерываем цикл
                if len(remnawave_users) >= total_users:
                    break
                    
            except Exception as e:
                elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                
                if elapsed_retry_time > max_retry_time:
                    logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек) для получения пользователей RemnaWave. Последняя ошибка: {str(e)}")
                    if not remnawave_users:  # Если не получили ни одного пользователя
                        return
                    break  # Если получили хотя бы часть пользователей, продолжаем с тем, что есть
                
                logger.warning(f"Ошибка при получении списка пользователей из RemnaWave (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)
        
        logger.debug(f"Всего получено {len(remnawave_users)} пользователей из RemnaWave")
        
        # Создаем словарь с UUID в качестве ключей
        remnawave_users_dict = {}
        for user in remnawave_users:
            uuid_key = user.get('uuid')
            if uuid_key:
                remnawave_users_dict[uuid_key] = user
                
        # Выполним проверку пользователей
        not_found_users = []
        for user in users_with_uuid:
            # Получаем UUID пользователя как строку
            user_uuid_str = str(user.remnawave_uuid)
            
            if user_uuid_str in remnawave_users_dict:
                remnawave_user = remnawave_users_dict[user_uuid_str]
                
                try:
                    remnawave_expire_at = datetime.fromisoformat(remnawave_user['expireAt'].replace('Z', '+00:00'))
                    db_expire_at = user.expired_at
                    
                    # Подробное логирование данных пользователя из RemnaWave
                    logger.debug(f"Данные пользователя {user.id} из RemnaWave: {remnawave_user}")
                    
                    # Проверяем подключения пользователя
                    online_at = remnawave_user.get('onlineAt')
                    logger.debug(f"Пользователь {user.id}: onlineAt={online_at}, текущий connected_at={user.connected_at}")
                    
                    if online_at:
                        new_connected_at = datetime.fromisoformat(online_at.replace('Z', '+00:00'))
                        old_connected_at = user.connected_at
                        
                        # Если раньше не было подключений, а теперь есть - вызываем on_activated_key
                        if not old_connected_at:
                            referrer = await user.referrer()
                            await on_activated_key(
                                user.id,
                                user.full_name,
                                referrer_id=referrer.id if referrer else None,
                                referrer_name=referrer.full_name if referrer else None
                            )
                            user.is_registered = True
                            await user.save()
                            logger.info(f"Первое подключение пользователя {user.id}, отправлено уведомление")
                        
                        # Обновляем только если время онлайна новее или connected_at отсутствует
                        if not old_connected_at or new_connected_at > old_connected_at:
                            user.connected_at = new_connected_at
                            await user.save()
                            # Записываем подключение в таблицу connections
                            await Connections.process(user.id, new_connected_at.date())
                            logger.debug(f"Обновлен статус подключения для пользователя {user.id}: {new_connected_at}")
                        else:
                            logger.debug(f"Пропуск обновления для пользователя {user.id}: текущее время подключения новее ({old_connected_at} > {new_connected_at})")
                    else:
                        logger.debug(f"Пользователь {user.id} не имеет активных подключений")
                    
                    # Текущая дата по MSK для проверки прошлых дат
                    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
                    # Конвертим полученное expireAt в MSK и берём дату для сравнения
                    remnawave_expire_date = remnawave_expire_at.astimezone(ZoneInfo("Europe/Moscow")).date()
                    db_expire_date = db_expire_at
                    
                    # Пропускаем обновление, если дата истечения в прошлом
                    if db_expire_date < today:
                        logger.warning(f"Дата истечения {db_expire_date} находится в прошлом. RemnaWave не принимает такие даты. Пропускаем обновление для пользователя {user.id}")
                        continue
                    
                    if db_expire_date != remnawave_expire_date:
                        logger.debug(f"Обновление даты истечения в RemnaWave для пользователя {user.id}: {remnawave_expire_at} -> {db_expire_at}")
                        # Передаём дату из БД напрямую, клиент сам форматирует expireAt
                        result = await remnawave.users.update_user(user_uuid_str, expireAt=db_expire_at)
                        update_success = True
                        updated += 1
                except Exception as e:
                    logger.error(f"Ошибка при обработке пользователя {user.id}: {str(e)}")
                    errors += 1
            else:
                logger.warning(f"Пользователь {user.id} с UUID {user_uuid_str} не найден в RemnaWave")
                not_found_users.append(user)
                
        if not_found_users:
            logger.warning(f"Найдено {len(not_found_users)} пользователей, которые есть в БД, но отсутствуют в RemnaWave")
            
            # Попробуем получить каждого пользователя напрямую по UUID
            for user in not_found_users:
                try:
                    user_response = await remnawave.users.get_user_by_uuid(user.remnawave_uuid)
                    if user_response and "response" in user_response:
                        remnawave_users_dict[user.remnawave_uuid] = user_response["response"]
                        logger.info(f"Пользователь {user.id} успешно получен по прямому запросу")
                except Exception as e:
                    logger.warning(f"Не удалось получить пользователя {user.id} по UUID {user.remnawave_uuid}: {str(e)}")
        
        # Вычисляем время выполнения с учётом одинаковой tz-aware метки
        elapsed = (datetime.now(ZoneInfo("Europe/Moscow")) - start_time).total_seconds()
        logger.info(f"Синхронизация завершена за {elapsed:.2f} секунд. Обновлено: {updated}, ошибок: {errors}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка в remnawave_updater: {str(e)}")
    finally:
        update_in_progress = False
        # Лог завершения с использованием той же tz-aware даты
        end_time = datetime.now(ZoneInfo("Europe/Moscow"))
        total_time = (end_time - start_time).total_seconds()
        logger.info(f"Завершение работы remnawave_updater, время выполнения: {total_time:.2f} секунд") 
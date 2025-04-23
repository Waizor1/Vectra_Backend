import asyncio
from datetime import datetime

from pytz import UTC

from bloobcat.db.users import Users
from bloobcat.settings import remnawave_settings
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.logger import get_logger
from bloobcat.processing.remnawave_processor import process_user

logger = get_logger("remnawave_online")
remnawave = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())

async def check_online_status():
    """Периодически проверяет статус пользователей через API"""
    while True:
        try:
            logger.debug("Начало проверки онлайн статуса пользователей")
            
            # Получаем всех пользователей с UUID RemnaWave
            users = await Users.filter(remnawave_uuid__not_isnull=True).all()
            if not users:
                logger.debug("Нет пользователей с UUID RemnaWave")
                await asyncio.sleep(60)  # Ждем минуту перед следующей проверкой
                continue

            # Получаем статус всех пользователей
            try:
                response = await remnawave.users.get_users(size=100)  # Можно увеличить размер если нужно
                online_users = response.get("response", {}).get("users", [])
                online_users_dict = {str(user["uuid"]): user for user in online_users}
                
                for user in users:
                    remnawave_user = online_users_dict.get(str(user.remnawave_uuid))
                    if remnawave_user:
                        # Проверяем статус и обновляем время подключения
                        if remnawave_user.get("onlineAt"):
                            was_registered = user.is_registered
                            user.connected_at = datetime.now(UTC)
                            await user.save()
                            
                            if not was_registered:
                                try:
                                    logger.info(f"Прямой вызов process_user для пользователя {user.id}")
                                    asyncio.create_task(process_user(user))
                                except Exception as e:
                                    logger.error(f"Ошибка при прямом вызове process_user: {e}")
            
            except Exception as e:
                logger.error(f"Ошибка при получении данных из RemnaWave API: {e}")

        except Exception as e:
            logger.error(f"Ошибка в check_online_status: {e}")
        
        await asyncio.sleep(60)  # Проверяем каждую минуту

async def online_worker_tasks():
    """Запускает задачи мониторинга онлайн статуса"""
    try:
        tasks = []
        
        # Добавляем основную задачу мониторинга
        tasks.append(asyncio.create_task(check_online_status()))
        logger.info("Добавлена задача мониторинга онлайн статуса RemnaWave")
        
        return tasks
    except Exception as e:
        logger.error(f"Ошибка при инициализации задач мониторинга RemnaWave: {e}")
        return [] 
import asyncio
from datetime import datetime, timedelta
from cyberdog.logger import get_logger

# Инициализируем логгер для вспомогательных функций расписаний
logger = get_logger("schedules.helpers")

async def periodic_task(func, interval_seconds, task_name=None):
    """
    Запускает функцию периодически с заданным интервалом.
    
    Args:
        func: Асинхронная функция для выполнения
        interval_seconds: Интервал в секундах между запусками
        task_name: Опциональное имя задачи для логирования
    """
    name = task_name or func.__name__
    while True:
        try:
            start_time = datetime.now()
            logger.info(f"Запуск задачи {name}")
            await func()
            logger.info(f"Задача {name} завершена")
            
            # Вычисляем, сколько времени осталось до следующего запуска
            elapsed = (datetime.now() - start_time).total_seconds()
            sleep_time = max(0, interval_seconds - elapsed)
            logger.debug(f"Задача {name} будет запущена снова через {sleep_time:.2f} секунд")
            await asyncio.sleep(sleep_time)
        except Exception as e:
            logger.error(f"Ошибка в задаче {name}: {str(e)}")
            await asyncio.sleep(interval_seconds)  # В случае ошибки все равно ждем полный интервал

async def daily_task(func, hour, minute=0, task_name=None):
    """
    Запускает функцию ежедневно в указанное время.
    
    Args:
        func: Асинхронная функция для выполнения
        hour: Час запуска (0-23)
        minute: Минута запуска (0-59)
        task_name: Опциональное имя задачи для логирования
    """
    name = task_name or func.__name__
    while True:
        try:
            # Вычисляем время до следующего запуска
            now = datetime.now()
            target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now > target_time:
                # Если текущее время уже после целевого, переходим на следующий день
                target_time = target_time + timedelta(days=1)
            
            # Вычисляем время ожидания в секундах
            wait_seconds = (target_time - now).total_seconds()
            logger.info(f"Задача {name} будет запущена через {wait_seconds:.2f} секунд ({target_time.strftime('%Y-%m-%d %H:%M:%S')})")
            
            await asyncio.sleep(wait_seconds)
            
            # Запускаем задачу
            logger.info(f"Запуск ежедневной задачи {name}")
            await func()
            logger.info(f"Ежедневная задача {name} завершена")
            
            # Ждем немного, чтобы не запустить задачу дважды, если она выполнилась очень быстро
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Ошибка в ежедневной задаче {name}: {str(e)}")
            await asyncio.sleep(60)  # В случае ошибки ждем минуту и пробуем снова 
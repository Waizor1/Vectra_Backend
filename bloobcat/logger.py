import os
import sys
import logging
import datetime
from loguru import logger
from .settings import test_mode

# Функция для настройки логирования
def setup_logging():
    """
    Настройка логирования для всего приложения с использованием loguru.
    - Консольный вывод с цветным форматированием
    - Файлы логов с датой и временем создания
    - Отдельные файлы для логов платежей и туннеля
    - Перехват стандартных логов Python
    """
    # Определяем путь для логов с учетом Docker
    if os.environ.get('DOCKER_LOGS_PATH'):
        log_dir = os.environ.get('DOCKER_LOGS_PATH')
    else:
        # Если приложение запущено не в Docker
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
    
    # Создаем директорию для логов, если её нет
    os.makedirs(log_dir, exist_ok=True)
    
    # Удаляем стандартный обработчик логов Loguru
    logger.remove()
    
    # Устанавливаем уровень логирования
    default_log_level_based_on_test_mode = "DEBUG" if test_mode else "INFO"
    log_level_from_env = os.environ.get("LOG_LEVEL")
    
    if log_level_from_env:
        log_level = log_level_from_env.upper()
        # Проверка на допустимые значения, если нужно, но loguru обычно сам справляется
        # или можно добавить: if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        #                      log_level = default_log_level_based_on_test_mode 
    else:
        log_level = default_log_level_based_on_test_mode
    
    # Список фраз для фильтрации
    filtered_phrases = [
        "Обновлено время подключения для пользователя",
        "Не удалось получить статус пользователя",
        "Пользователь еще не подключался, но уже имеет статус",
        "Ответ на GET запрос к /user/"
    ]
    
    # Функция для фильтрации сообщений
    def message_filter(record):
        # Фильтрация по имени логгера и уровню
        if (record["name"].startswith(("apscheduler", "httpcore._trace")) and 
            record["level"].no < logger.level("WARNING").no):
            return False
            
        # Фильтрация по содержимому сообщения
        for phrase in filtered_phrases:
            if phrase in record["message"]:
                return False
                
                
        return True
    
    # Добавляем обработчик для консоли
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True,
        filter=message_filter
    )
    
    # Текущее время для имен файлов
    current_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    # Файловый sink. По умолчанию сохраняет историческое поведение:
    # имя файла включает timestamp старта (`bloobcat_2026-05-16_19-21-01.log`),
    # ротация по размеру 10 MB, retention = 30 файлов. При частых рестартах
    # это накапливает много файлов, поэтому есть опт-ин на daily rotation
    # через флаг OBSERVABILITY_LOG_DAILY_ROTATION_ENABLED=true. Включай
    # ТОЛЬКО после проверки что log-shipping pipeline не зависит от
    # текущего паттерна имени файла (timestamp-per-startup).
    daily_rotation_enabled = os.environ.get(
        "OBSERVABILITY_LOG_DAILY_ROTATION_ENABLED", "false"
    ).strip().lower() in ("true", "1", "yes", "on")

    if daily_rotation_enabled:
        logger.add(
            os.path.join(log_dir, "bloobcat.log"),
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level=log_level,
            rotation="00:00",            # daily в полночь
            retention="30 days",         # хранить 30 дней
            compression="zip",
            encoding="utf-8",
            filter=message_filter,
        )
    else:
        # Legacy: timestamp-per-startup имя + size-based rotation.
        logger.add(
            os.path.join(log_dir, f"bloobcat_{current_time}.log"),
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level=log_level,
            rotation="10 MB",
            compression="zip",
            retention=30,
            encoding="utf-8",
            filter=message_filter,
        )

    # ── Optional JSON sink ──────────────────────────────────────────────────
    # Дополнительный sink для машиночитаемых JSON-логов (Loki/ELK ingestion).
    # Параллельный, НЕ заменяет существующие stdout/file sinks. Включается
    # флагом OBSERVABILITY_LOG_JSON_SINK_ENABLED=true. Пишется в отдельный
    # файл bloobcat_json_*.log (не в stderr — pytest captures и может
    # перехватить вывод). При сбое этого sink (например, диск полный) два
    # существующих sink'а продолжают работать независимо.
    json_sink_enabled = os.environ.get(
        "OBSERVABILITY_LOG_JSON_SINK_ENABLED", "false"
    ).strip().lower() in ("true", "1", "yes", "on")
    if json_sink_enabled:
        # JSON sink honours the same daily-rotation flag so both sinks rotate
        # consistently. Two filename patterns to stay backward-compatible with
        # whatever the operator currently ships.
        if daily_rotation_enabled:
            logger.add(
                os.path.join(log_dir, "bloobcat_json.log"),
                serialize=True,
                level=log_level,
                rotation="00:00",
                retention="30 days",
                compression="zip",
                encoding="utf-8",
                filter=message_filter,
            )
        else:
            logger.add(
                os.path.join(log_dir, f"bloobcat_json_{current_time}.log"),
                serialize=True,           # JSON output (Loguru built-in)
                level=log_level,
                rotation="10 MB",
                compression="zip",
                retention=30,
                encoding="utf-8",
                filter=message_filter,    # тот же фильтр — иначе spam утечёт в JSON
            )

    # Класс для перехвата стандартных логов Python
    class InterceptHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.level_mapping = {
                50: "CRITICAL",
                40: "ERROR",
                30: "WARNING",
                20: "INFO",
                10: "DEBUG",
                0: "NOTSET",
            }
    
        def emit(self, record):
            # Получаем соответствующий уровень логирования Loguru
            level = self.level_mapping.get(record.levelno, record.levelname)
            
            # Определяем правильный фрейм
            frame = None
            depth = 6
            
            while frame is None:
                try:
                    frame = sys._getframe(depth)
                    if frame.f_code.co_filename == logging.__file__:
                        frame = None
                        depth += 1
                        continue
                except ValueError:
                    frame = sys._getframe(1)
                    break
            
            # Передаем сообщение в Loguru
            logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )
    
    # Настройка перехвата стандартных логов
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # Явно отключаем/повышаем уровни логов некоторых модулей
    for logger_name in [
        'sqlalchemy', 
        'sqlalchemy.engine', 
        'sqlalchemy.pool', 
        'sqlalchemy.dialects', 
        'sqlalchemy.orm',
        'sqlalchemy.engine.base.Engine',
        'httpx',
        'asyncio',
        'apscheduler',
        'httpcore',
        'httpcore._trace'
    ]:
        logging.getLogger(logger_name).setLevel(logging.ERROR)
        logging.getLogger(logger_name).propagate = False
    # Подавляем информационные логи aiogram
    for logger_name in [
        'aiogram',
        'aiogram.dispatcher',
        'aiogram.dispatcher.dispatcher'
    ]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
        logging.getLogger(logger_name).propagate = False
    
    # Повышаем уровень для некоторых библиотек
    for logger_name in ['tortoise.db_client', 'websockets.client']:
        logging.getLogger(logger_name).setLevel(logging.INFO)
    

    
    return logger

# Создаем и экспортируем предварительно настроенный логгер
configured_logger = setup_logging()

def get_payment_logger():
    """Получить логгер для платежей"""
    return configured_logger.bind(
        name="payment",
        payment_id="",
        user_id="",
        amount="",
        status=""
    )

def get_tunnel_logger():
    """Получить логгер для туннеля"""
    return configured_logger.bind(name="tunnel")

def get_logger(name: str):
    """Получить логгер по имени"""
    return configured_logger.bind(name=name) 
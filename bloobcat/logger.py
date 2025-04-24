import os
import sys
import logging
import datetime
from loguru import logger

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
        level="INFO",
        colorize=True,
        filter=message_filter
    )
    
    # Текущее время для имен файлов
    current_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    
    # Добавляем обработчик для основного файла логов
    logger.add(
        os.path.join(log_dir, f"bloobcat_{current_time}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="INFO",
        rotation="10 MB",
        compression="zip",
        retention=30,
        encoding="utf-8",
        filter=lambda record: message_filter(record) and record["name"] not in ["payment", "tunnel"]
    )
    
    # Добавляем обработчик для логов платежей
    payment_logger_id = logger.add(
        os.path.join(log_dir, f"payments_{current_time}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - [PaymentID:{extra[payment_id]}] [UserID:{extra[user_id]}] [Amount:{extra[amount]}] [Status:{extra[status]}] {message}",
        level="INFO",
        rotation="10 MB",
        compression="zip",
        retention=30,
        encoding="utf-8",
        filter=lambda record: record["name"] == "payment"
    )
    
    # Добавляем обработчик для логов туннеля
    tunnel_logger_id = logger.add(
        os.path.join(log_dir, f"tunnel_{current_time}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="INFO",
        rotation="10 MB", 
        compression="zip",
        retention=30,
        encoding="utf-8", 
        filter=lambda record: record["name"] == "tunnel"
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
    
    # Явно отключаем все логи некоторых модулей
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
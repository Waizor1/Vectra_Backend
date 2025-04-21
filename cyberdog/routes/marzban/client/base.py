from httpx import AsyncClient, Timeout

from cyberdog.settings import marzban_settings
from cyberdog.logger import get_logger

logger = get_logger("marzban_client.base")

class BaseMarzbanClient:
    """Базовый клиент для взаимодействия с API Marzban."""
    def __init__(self, timeout: int = 300):
        logger.info(f"Инициализация базового клиента Marzban с URL: {marzban_settings.url}")
        
        if not marzban_settings.url:
            logger.error("URL Marzban не настроен")
            raise ValueError("MARZBAN_URL must be set in environment variables")
            
        if not marzban_settings.token:
            logger.error("Токен Marzban не настроен")
            raise ValueError("MARZBAN_TOKEN must be set in environment variables")
            
        try:
            # Используем объект Timeout для явного указания таймаутов
            timeout_config = Timeout(timeout, connect=60.0) # Общий таймаут, таймаут соединения
            
            self.client = AsyncClient(
                base_url=marzban_settings.url + "/api",
                headers={
                    "Authorization": "Bearer "
                    + marzban_settings.token.get_secret_value()
                },
                timeout=timeout_config,
                proxies=[], # Явно указываем пустой список прокси
            )
            logger.info(f"Базовый клиент Marzban успешно инициализирован с таймаутом {timeout} секунд")
        except Exception as e:
            logger.error(f"Ошибка инициализации базового клиента Marzban: {str(e)}", exc_info=True)
            raise

    def get_user_with_params(self, user_data: dict, allowed_params: list) -> dict:
        """Фильтрует словарь с данными пользователя, оставляя только разрешенные параметры."""
        filtered_data = {}
        for key, value in user_data.items():
            if key in allowed_params:
                filtered_data[key] = value
        return filtered_data

    async def get(self, path: str, params: dict = None):
        """Выполняет GET запрос к API Marzban."""
        try:
            # Используем DEBUG для часто повторяющихся запросов
            log_level = logger.debug if path.startswith(tuple(["/users", "/nodes", "/inbounds"])) else logger.info
            log_msg = f"GET запрос к {path}"
            if params:
                log_msg += f" с параметрами: {params}"
            log_level(log_msg)
                
            response = await self.client.get(path, params=params)
            
            log_level(f"Ответ на GET запрос к {path}: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"Ошибка GET запроса к {path}: {str(e)}", exc_info=True)
            raise

    async def post(self, path: str, data: dict):
        """Выполняет POST запрос к API Marzban."""
        try:
            logger.info(f"POST запрос к {path} с данными: {data}")
            response = await self.client.post(path, json=data)
            logger.info(f"Ответ на POST запрос к {path}: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"Ошибка POST запроса к {path}: {str(e)}", exc_info=True)
            raise

    async def put(self, path: str, data: dict):
        """Выполняет PUT запрос к API Marzban."""
        try:
            logger.info(f"PUT запрос к {path} с данными: {data}")
            response = await self.client.put(path, json=data)
            logger.info(f"Ответ на PUT запрос к {path}: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"Ошибка PUT запроса к {path}: {str(e)}", exc_info=True)
            raise

    async def delete(self, path: str):
        """Выполняет DELETE запрос к API Marzban."""
        try:
            logger.info(f"DELETE запрос к {path}")
            response = await self.client.delete(path)
            logger.info(f"Ответ на DELETE запрос к {path}: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"Ошибка DELETE запроса к {path}: {str(e)}", exc_info=True)
            raise

    async def close(self):
        """Закрывает HTTP клиент."""
        if hasattr(self, 'client') and self.client:
            await self.client.aclose()
            logger.info("HTTP клиент Marzban закрыт") 
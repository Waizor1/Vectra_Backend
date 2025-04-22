"""Модуль клиента для взаимодействия с API Marzban."""

from bloobcat.logger import get_logger
from .base import BaseMarzbanClient
from .users import UsersClient
from .infra import InfraClient

logger = get_logger("marzban_client")

# Экспортируем основной класс клиента
__all__ = ["MarzbanClient"]

class MarzbanClient:
    """
    Основной клиент Marzban, объединяющий доступ к различным ресурсам API.
    
    Предоставляет доступ к под-клиентам через атрибуты:
    - `users`: для работы с пользователями
    - `infra`: для работы с inbounds и nodes
    - `base`: для доступа к базовым методам (get, post, put, delete), если необходимо
    """
    def __init__(self, timeout: int = 300):
        logger.info(f"Инициализация основного клиента Marzban (timeout={timeout}s)")
        try:
            self.base = BaseMarzbanClient(timeout=timeout)
            self.users = UsersClient(self.base)
            self.infra = InfraClient(self.base)
            logger.info("Основной клиент Marzban и его под-клиенты успешно инициализированы.")
        except Exception as e:
            logger.error(f"Критическая ошибка при инициализации основного клиента Marzban: {str(e)}", exc_info=True)
            # Можно либо перевыбросить исключение, либо установить флаг ошибки,
            # чтобы приложение знало, что клиент не готов к работе.
            # Пока просто перевыбрасываем.
            raise

    async def close(self):
        """Закрывает базовый HTTP клиент."""
        await self.base.close() 
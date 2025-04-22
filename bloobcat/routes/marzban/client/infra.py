from typing import List, Dict, Any

from bloobcat.logger import get_logger
from .base import BaseMarzbanClient

logger = get_logger("marzban_client.infra")

class InfraClient:
    """Клиент для взаимодействия с эндпоинтами инфраструктуры API Marzban (inbounds, nodes)."""
    def __init__(self, base_client: BaseMarzbanClient):
        self.base_client = base_client

    async def get_inbounds(self) -> List[Dict[str, Any]]:
        """Получает список входящих соединений (inbounds) из Marzban."""
        try:
            logger.debug("Запрос списка входящих соединений (/inbounds)")
            response = await self.base_client.get("/inbounds")
            if response.status_code == 200:
                inbounds = response.json()
                logger.debug(f"Получено {len(inbounds)} входящих соединений")
                return inbounds
            else:
                logger.warning(f"Неуспешный ответ ({response.status_code}) при получении inbounds: {response.text[:200]}")
                return []
        except Exception as e:
            logger.error(f"Ошибка получения списка inbounds: {str(e)}", exc_info=True)
            return []

    async def get_nodes(self) -> List[Dict[str, Any]]:
        """Получает список нод (серверов) из Marzban."""
        try:
            logger.debug("Запрос списка нод (/nodes)")
            response = await self.base_client.get("/nodes")
            if response.status_code == 200:
                data = response.json()
                # API может вернуть словарь с ключом 'nodes' или просто список
                if isinstance(data, dict) and "nodes" in data and isinstance(data["nodes"], list):
                    nodes = data["nodes"]
                    logger.debug(f"Получено {len(nodes)} нод (из словаря)")
                    return nodes
                elif isinstance(data, list):
                    logger.debug(f"Получено {len(data)} нод (из списка)")
                    return data
                else:
                    logger.warning(f"Неожиданный формат ответа при получении нод: {type(data)}")
                    return []
            else:
                logger.warning(f"Неуспешный ответ ({response.status_code}) при получении нод: {response.text[:200]}")
                return []
        except Exception as e:
            logger.error(f"Ошибка получения списка нод: {str(e)}", exc_info=True)
            return [] 
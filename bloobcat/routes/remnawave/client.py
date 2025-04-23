import aiohttp
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, date, time, timezone, timedelta
from bloobcat.logger import get_logger
from bloobcat.db.users import Users  # Добавляем импорт Users

logger = get_logger("remnawave_client")

class RemnaWaveClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.token}',
        }
        self.session = None
        self.users = UsersAPI(self)
        self.nodes = NodesAPI(self)
        self.inbounds = InboundsAPI(self)

    async def _ensure_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(headers=self.headers)

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        await self._ensure_session()
        url = f"{self.base_url}{endpoint}"
        
        request_data_log = kwargs.get('json', kwargs.get('data', {}))
        logger.debug(f"Sending {method} request to {url} with data: {json.dumps(request_data_log, indent=2, default=str)}")
        
        try:
            async with self.session.request(method, url, **kwargs) as response:
                if response.status == 401:
                    raise Exception("Unauthorized - Invalid API token")
                
                response_json = await response.json()
                if response.status >= 400:
                    error_msg = response_json.get('message', 'Unknown error')
                    logger.error(f"API Error Response ({response.status}) from {url}: {response_json}")
                    raise Exception(f"API error: {error_msg}")
                
                return response_json
        except aiohttp.ClientError as e:
            logger.error(f"Network error while calling {url}: {str(e)}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response from {url}: {str(e)}")
            logger.error(f"Raw response text: {await response.text()}")
            raise Exception("Failed to decode API response")

class UsersAPI:
    def __init__(self, client: RemnaWaveClient):
        self.client = client

    async def create_user(self, username: str, traffic_limit_bytes: int = 0,
                         expire_at: Optional[date] = None,
                         description: str = None,
                         telegram_id: int = None, email: str = None,
                         hwid_device_limit: int = None) -> Dict[str, Any]:
        """Создание пользователя с указанием лимита устройств"""

        expire_at_dt_str: Optional[str] = None
        if expire_at:
            expire_at_dt = datetime.combine(expire_at, time.max, tzinfo=timezone.utc)
            expire_at_dt_str = expire_at_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + 'Z'
            logger.debug(f"Formatted expireAt date for API: {expire_at_dt_str}")

        data = {
            "username": username,
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": expire_at_dt_str,
            "description": description,
            "telegramId": telegram_id,
            "email": email,
            "hwidDeviceLimit": hwid_device_limit
        }
        filtered_data = {k: v for k, v in data.items() if v is not None}

        return await self.client._request("POST", "/api/users", json=filtered_data)

    async def get_users(self, size: int = 100, start: int = 0) -> Dict[str, Any]:
        """Получение списка пользователей"""
        return await self.client._request("GET", f"/api/users/v2?size={size}&start={start}")

    async def get_user_by_uuid(self, uuid: str) -> Dict[str, Any]:
        """Получение пользователя по UUID"""
        return await self.client._request("GET", f"/api/users/uuid/{uuid}")

    async def update_user(self, uuid: str, **kwargs) -> Dict[str, Any]:
        """Обновление пользователя"""
        return await self.client._request("POST", "/api/users/update", json={"uuid": uuid, **kwargs})

    async def delete_user(self, uuid: str) -> Dict[str, Any]:
        """Удаление пользователя"""
        return await self.client._request("DELETE", f"/api/users/delete/{uuid}")

    async def get_subscription_url(self, user_db: Users) -> str:
        """Получает URL подписки пользователя (только если он уже существует в RemnaWave)."""
        logger.info(f"Получаем URL подписки для пользователя {user_db.id}")
        
        if not user_db.remnawave_uuid:
            logger.error(f"У пользователя {user_db.id} отсутствует UUID RemnaWave. Невозможно получить URL.")
            raise ValueError(f"User {user_db.id} has no RemnaWave UUID")
        
        try:
            logger.info(f"Запрос данных пользователя по UUID: {user_db.remnawave_uuid}")
            user_data = await self.get_user_by_uuid(user_db.remnawave_uuid)
            subscription_url = user_data["response"].get("happ", {}).get("cryptoLink", "")
            
            if not subscription_url:
                logger.error(f"В данных пользователя {user_db.id} (UUID: {user_db.remnawave_uuid}) отсутствует ссылка. Данные: {user_data}")
                raise ValueError("Subscription URL not found in user data")
            
            return subscription_url
        except Exception as e:
            if "User not found" in str(e) or "404" in str(e):
                # UUID есть, но пользователь не найден в RemnaWave - это ошибка синхронизации
                logger.error(f"Пользователь с UUID {user_db.remnawave_uuid} не найден в RemnaWave, но UUID есть в БД")
                raise ValueError(f"User with UUID {user_db.remnawave_uuid} not found in RemnaWave") 
            raise

    async def get_user_hwid_devices(self, user_uuid: str) -> Dict[str, Any]:
        """Получение списка HWID устройств пользователя"""
        return await self.client._request("GET", f"/api/hwid/devices/get/{user_uuid}")

    async def add_user_hwid_device(self, user_uuid: str, hwid: str, 
                                 platform: str = None, os_version: str = None,
                                 device_model: str = None, user_agent: str = None) -> Dict[str, Any]:
        """Добавление HWID устройства пользователю"""
        data = {
            "userUuid": user_uuid,
            "hwid": hwid,
            "platform": platform,
            "osVersion": os_version,
            "deviceModel": device_model,
            "userAgent": user_agent
        }
        return await self.client._request("POST", "/api/hwid/devices/create", json=data)

    async def delete_user_hwid_device(self, user_uuid: str, hwid: str) -> Dict[str, Any]:
        """Удаление HWID устройства пользователя"""
        data = {
            "userUuid": user_uuid,
            "hwid": hwid
        }
        return await self.client._request("POST", "/api/hwid/devices/delete", json=data)

    async def update_user_hwid_limit(self, uuid: str, hwid_limit: int) -> Dict[str, Any]:
        """Обновление лимита HWID устройств пользователя"""
        return await self.update_user(uuid, hwidDeviceLimit=hwid_limit)

class NodesAPI:
    def __init__(self, client: RemnaWaveClient):
        self.client = client

    async def get_nodes(self) -> Dict[str, Any]:
        return await self.client._request("GET", "/api/nodes/get-all")

    async def get_node(self, uuid: str) -> Dict[str, Any]:
        return await self.client._request("GET", f"/api/nodes/get-one/{uuid}")

class InboundsAPI:
    def __init__(self, client: RemnaWaveClient):
        self.client = client

    async def get_inbounds(self) -> Dict[str, Any]:
        return await self.client._request("GET", "/api/inbounds")

    async def get_full_inbounds(self) -> Dict[str, Any]:
        return await self.client._request("GET", "/api/inbounds/full") 
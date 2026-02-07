import aiohttp
import json
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, date, time, timezone, timedelta
from bloobcat.logger import get_logger
from bloobcat.db.users import Users  # Добавляем импорт Users
from zoneinfo import ZoneInfo

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
        self.tools = ToolsAPI(self)

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
        
        # Обрабатываем JSON данные для сериализации UUID
        if 'json' in kwargs:
            kwargs['json'] = self._prepare_json_data(kwargs['json'])
        
        request_data_log = kwargs.get('json', kwargs.get('data', {}))
        logger.debug(f"Sending {method} request to {url} with data: {json.dumps(request_data_log, indent=2, default=str)}")
        
        try:
            async with self.session.request(method, url, **kwargs) as response:
                if response.status == 401:
                    raise Exception("Unauthorized - Invalid API token")
                
                try:
                    response_json = await response.json()
                except json.JSONDecodeError as e:
                    response_text = await response.text()
                    logger.error(f"Failed to decode JSON response from {url}: {str(e)}")
                    logger.error(f"Raw response text: {response_text}")
                    raise Exception(f"Failed to decode API response: {response_text}")
                
                if response.status >= 400:
                    error_msg = response_json.get('message', 'Unknown error')
                    error_code = response_json.get('errorCode')
                    logger.error(f"API Error Response ({response.status}) from {url}: {response_json}")
                    # Новая панель возвращает детали валидации в errors[]. Важно пометить это как validation,
                    # чтобы верхний retry-слой не тратил 60 сек на бессмысленные повторы.
                    if response.status in (400, 422) and isinstance(response_json.get("errors"), list):
                        raise Exception(f"Validation error: {error_msg}")
                    # Включаем errorCode в текст исключения, чтобы можно было на него ориентироваться выше
                    if error_code:
                        raise Exception(f"API error [{error_code}]: {error_msg}")
                    raise Exception(f"API error [{response.status}]: {error_msg}")
                
                return response_json
        except aiohttp.ClientError as e:
            logger.error(f"Network error while calling {url}: {str(e)}")
            raise Exception(f"Network error: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response from {url}: {str(e)}")
            logger.error(f"Raw response text: {await response.text()}")
            raise Exception("Failed to decode API response")
            
    def _prepare_json_data(self, data):
        """Подготовка данных для сериализации в JSON (преобразование UUID и других типов)"""
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                result[key] = self._prepare_json_data(value)
            return result
        elif isinstance(data, list):
            return [self._prepare_json_data(item) for item in data]
        elif hasattr(data, 'hex') and callable(getattr(data, 'hex')):  # UUID objects
            return str(data)
        else:
            return data

class UsersAPI:
    def __init__(self, client: RemnaWaveClient):
        self.client = client

    def _format_expire_at(self, expire_at: date) -> str:
        """Форматируем expireAt как 00:00 в зоне Europe/Moscow и конвертим в UTC для API"""
        local_tz = ZoneInfo("Europe/Moscow")
        # начало дня по МСК
        local_midnight = datetime.combine(expire_at, time.min, tzinfo=local_tz)
        # переводим в UTC
        expire_at_dt = local_midnight.astimezone(timezone.utc)
        return expire_at_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + 'Z'

    async def _execute_with_retry(self, func, *args, **kwargs) -> Dict[str, Any]:
        """Выполняет функцию с повторными попытками в течение 60 сек, пропускает валидационные ошибки"""
        start_time = datetime.now()
        retry_interval = 3
        max_total_time = 60
        while True:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # Не повторяем, если у этой панели нет эндпоинта статистики usage
                if "api/users/stats/usage" in str(e) and "404" in str(e):
                    raise

                # Не повторяем в случае валидационной ошибки
                if 'validation' in str(e).lower():
                    raise
                
                # Не повторяем в случае ошибок удаления HWID устройств (A101)
                if 'A101' in str(e) or 'Delete hwid user device error' in str(e):
                    logger.debug(f"Пропускаем повторные попытки для ошибки удаления HWID: {e}")
                    raise
                
                # Не повторяем в случае ошибки дублирующегося username (A019)
                if 'A019' in str(e) or 'User username already exists' in str(e):
                    logger.debug(f"Пропускаем повторные попытки для ошибки дублирующегося username: {e}")
                    raise

                # Не повторяем для "пользователь не найден" (404/A063), чтобы быстрее перейти к пересозданию
                if 'A063' in str(e) or 'User with specified params not found' in str(e):
                    logger.debug(f"Пропускаем повторные попытки для ошибки не найденного пользователя: {e}")
                    raise
                
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > max_total_time:
                    raise
                logger.warning(f"Ошибка RemnaWave API: {e}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)

    async def create_user(
        self,
        username: str,
        expire_at: date,
        traffic_limit_strategy: str = "NO_RESET",
        status: str = "ACTIVE",
        traffic_limit_bytes: int = 0,
        description: str = None,
        telegram_id: int = None,
        email: str = None,
        hwid_device_limit: int = None,
        active_internal_squads: Optional[List[str]] = None,
        external_squad_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Создание пользователя с указанием лимита устройств"""

        # Формируем expireAt через централизованный метод
        expire_at_dt_str = self._format_expire_at(expire_at)
        logger.debug(f"Formatted expireAt date for API (adjusted to UTC): {expire_at_dt_str}")

        data = {
            "username": username,
            "status": status,
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": traffic_limit_strategy,
            "expireAt": expire_at_dt_str,
            "description": description,
            "telegramId": telegram_id,
            "email": email,
            "hwidDeviceLimit": hwid_device_limit,
            # v2.0.8: вместо activateAllInbounds используем внутренние сквады
            "activeInternalSquads": active_internal_squads,
            # External squad (optional)
            "externalSquadUuid": external_squad_uuid,
        }
        filtered_data = {k: v for k, v in data.items() if v is not None}

        # Выполняем с повторными попытками
        return await self._execute_with_retry(
            self.client._request, "POST", "/api/users", json=filtered_data
        )

    async def get_users(self, size: int = 100, start: int = 0) -> Dict[str, Any]:
        """Получение списка пользователей"""
        return await self._execute_with_retry(self.client._request, "GET", f"/api/users?size={size}&start={start}")

    async def get_user_by_uuid(self, uuid: str) -> Dict[str, Any]:
        """Получение пользователя по UUID"""
        logger.debug(f"Получение пользователя по UUID: {uuid}")
        response = await self._execute_with_retry(self.client._request, "GET", f"/api/users/{uuid}")
        logger.debug(f"Ответ при получении пользователя по UUID: {response}")
        return response

    async def get_user_usage_by_range(self, uuid: str, start: str, end: str) -> Dict[str, Any]:
        """Получение статистики трафика пользователя по диапазону дат"""
        return await self._execute_with_retry(
            self.client._request,
            "GET",
            f"/api/users/stats/usage/{uuid}/range?start={start}&end={end}",
        )

    async def update_user(self, uuid: str, **kwargs) -> Dict[str, Any]:
        """Обновление пользователя"""
        logger.debug(f"Обновление пользователя {uuid} с параметрами: {kwargs}")
        # Если дата истечения в прошлом или сегодня — ставим expireAt на текущее время +1 минуту по МСК
        if 'expireAt' in kwargs and isinstance(kwargs['expireAt'], date):
            moscow_tz = ZoneInfo("Europe/Moscow")
            today = datetime.now(moscow_tz).date()
            # Нормализуем expireAt к date для безопасного сравнения
            expire_val = kwargs['expireAt']
            expire_date = expire_val.date() if isinstance(expire_val, datetime) else expire_val
            if expire_date <= today:
                # Формируем новое время: текущее +1 минута по МСК и конвертим в UTC
                async def _bump_expire():
                    now_msk = datetime.now(moscow_tz)
                    future_msk = now_msk + timedelta(minutes=1)
                    future_utc = future_msk.astimezone(timezone.utc)
                    expire_str = future_utc.strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + 'Z'
                    # Преобразуем uuid в строку для сериализации
                    u = str(uuid) if hasattr(uuid, 'hex') else uuid
                    return await self.client._request(
                        "PATCH", "/api/users", json={"uuid": u, "expireAt": expire_str}
                    )
                return await self._execute_with_retry(_bump_expire)
        # Преобразуем UUID
        if hasattr(uuid, 'hex'):
            uuid = str(uuid)
        # Собираем данные
        data = {"uuid": uuid}
        for key, value in kwargs.items():
            if key == 'expireAt' and isinstance(value, date):
                # Нормализуем к date перед форматированием (datetime наследуется от date)
                normalized = value.date() if isinstance(value, datetime) else value
                data['expireAt'] = self._format_expire_at(normalized)
            else:
                data[key] = value
        logger.debug(f"Данные для API: {data}")
        # Выполняем обновление с повторными попытками
        return await self._execute_with_retry(
            self.client._request, "PATCH", "/api/users", json=data
        )

    async def delete_user(self, uuid: str) -> Dict[str, Any]:
        """Удаление пользователя"""
        return await self._execute_with_retry(self.client._request, "DELETE", f"/api/users/{uuid}")

    async def get_subscription_url(self, user_db: Users) -> str:
        """Получает URL подписки пользователя (только если он уже существует в RemnaWave)."""
        logger.debug(f"Получаем URL подписки для пользователя {user_db.id}")
        
        if not user_db.remnawave_uuid:
            logger.error(f"У пользователя {user_db.id} отсутствует UUID RemnaWave. Невозможно получить URL.")
            raise ValueError(f"User {user_db.id} has no RemnaWave UUID")
        
        try:
            logger.debug(f"Запрос данных пользователя по UUID: {user_db.remnawave_uuid}")
            user_data = await self.get_user_by_uuid(user_db.remnawave_uuid)
            resp = user_data.get("response") or {}

            # В новой панели happ.cryptoLink может отсутствовать (в OpenAPI он больше не required).
            # Fallback: берём subscriptionUrl и шифруем через /api/system/tools/happ/encrypt.
            subscription_url = (resp.get("happ") or {}).get("cryptoLink") or ""
            if not subscription_url:
                raw_sub_url = resp.get("subscriptionUrl") or ""
                if raw_sub_url:
                    subscription_url = await self.client.tools.encrypt_happ_crypto_link(raw_sub_url)
            
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
        return await self._execute_with_retry(self.client._request, "GET", f"/api/hwid/devices/{user_uuid}")

    async def get_hwid_devices(self, start: int = 0, size: int = 100) -> Dict[str, Any]:
        """Постраничное получение HWID устройств всех пользователей"""
        params = {"start": start, "size": size}
        return await self._execute_with_retry(self.client._request, "GET", "/api/hwid/devices", params=params)

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
        return await self._execute_with_retry(self.client._request, "POST", "/api/hwid/devices", json=data)

    async def delete_user_hwid_device(self, user_uuid: str, hwid: str) -> Dict[str, Any]:
        """Удаление HWID устройства пользователя"""
        data = {
            "userUuid": user_uuid,
            "hwid": hwid
        }
        return await self._execute_with_retry(self.client._request, "POST", "/api/hwid/devices/delete", json=data)

    async def update_user_hwid_limit(self, uuid: str, hwid_limit: int) -> Dict[str, Any]:
        """Обновление лимита HWID устройств пользователя"""
        return await self.update_user(uuid, hwidDeviceLimit=hwid_limit)

    async def revoke_user(self, uuid: str) -> Dict[str, Any]:
        """Отзывает подписку пользователя в RemnaWave по UUID"""
        logger.info(f"Revoking subscription for user {uuid}")
        if hasattr(uuid, 'hex'):
            uuid = str(uuid)
        return await self._execute_with_retry(self.client._request, "POST", f"/api/users/{uuid}/actions/revoke")

class NodesAPI:
    def __init__(self, client: RemnaWaveClient):
        self.client = client

    async def get_nodes(self) -> Dict[str, Any]:
        return await self.client._request("GET", "/api/nodes")

    async def get_node(self, uuid: str) -> Dict[str, Any]:
        return await self.client._request("GET", f"/api/nodes/{uuid}")

    async def get_nodes_usage_by_range(self, start: str, end: str) -> Dict[str, Any]:
        return await self.client._request(
            "GET", f"/api/nodes/usage/range?start={start}&end={end}"
        )

    async def get_node_user_usage_by_range(self, uuid: str, start: str, end: str) -> Dict[str, Any]:
        return await self.client._request(
            "GET", f"/api/nodes/usage/{uuid}/users/range?start={start}&end={end}"
        )

class InboundsAPI:
    def __init__(self, client: RemnaWaveClient):
        self.client = client

    async def get_inbounds(self) -> Dict[str, Any]:
        # v2.0.8: inbounds переехали под config-profiles
        # Возвращаем сразу агрегированный список инбаундов со всех профилей
        return await self.client._request("GET", "/api/config-profiles/inbounds")

    async def get_full_inbounds(self) -> Dict[str, Any]:
        # В v2.0.8 отдельного full-эндпоинта нет; используем тот же агрегированный
        return await self.client._request("GET", "/api/config-profiles/inbounds")


class ToolsAPI:
    def __init__(self, client: RemnaWaveClient):
        self.client = client

    async def encrypt_happ_crypto_link(self, link_to_encrypt: str) -> str:
        """Шифрует subscriptionUrl в encryptedLink через RemnaWave tool /api/system/tools/happ/encrypt."""
        raw_resp = await self.client._request(
            "POST",
            "/api/system/tools/happ/encrypt",
            json={"linkToEncrypt": link_to_encrypt},
        )
        encrypted = (raw_resp.get("response") or {}).get("encryptedLink") or ""
        if not encrypted:
            raise ValueError("Encrypted link not found in encrypt tool response")
        return encrypted

import asyncio
from typing import Optional, List, Dict, Any

from bloobcat.db.users import Users
from bloobcat.settings import marzban_settings
from bloobcat.logger import get_logger
from .base import BaseMarzbanClient

logger = get_logger("marzban_client.users")

class UsersClient:
    """Клиент для взаимодействия с пользовательскими эндпоинтами API Marzban."""
    def __init__(self, base_client: BaseMarzbanClient):
        self.base_client = base_client
        # Параметры, которые используются при обновлении пользователя PUT запросом
        # Важно сохранять только те параметры, которые мы контролируем
        self.allowed_user_update_params = [
            "status",
            "username",
            "note",
            "proxies",
            # "data_limit", # Убрано, так как трафик безлимитный
            "expire",
            # "data_limit_reset_strategy", # Убрано
            "inbounds",
        ]

    async def get_user(self, user_db: Users) -> Dict[str, Any]:
        """Получает данные одного пользователя из Marzban по ID из базы данных."""
        try:
            response = await self.base_client.get(f"/user/{user_db.id}")
            
            if response.status_code == 404:
                logger.warning(f"Пользователь {user_db.id} не найден в Marzban")
                raise ValueError("User not found in Marzban")
                
            if response.status_code != 200:
                logger.error(f"Ошибка получения пользователя {user_db.id}. Статус: {response.status_code}, Ответ: {response.text}")
                raise ValueError(f"Failed to get user. Status: {response.status_code}")
                
            return response.json()
                
        except Exception as e:
            if not isinstance(e, ValueError) or "User not found in Marzban" not in str(e):
                logger.error(f"Исключение при получении пользователя {user_db.id}: {str(e)}", exc_info=True)
            raise

    async def limit_user(self, user_db: Users) -> None:
        """Устанавливает статус 'disabled' для пользователя в Marzban."""
        try:
            logger.info(f"Ограничение пользователя {user_db.id}")
            user_data = await self.get_user(user_db)
            filtered_data = self.base_client.get_user_with_params(user_data, self.allowed_user_update_params)
            filtered_data["status"] = "disabled"
            await self.base_client.put(f"/user/{user_db.id}", filtered_data)
            logger.info(f"Пользователь {user_db.id} успешно ограничен")
        except Exception as e:
            logger.error(f"Ошибка при ограничении пользователя {user_db.id}: {str(e)}", exc_info=True)
            raise

    async def unlimit_user(self, user_db: Users) -> None:
        """Устанавливает статус 'active' для пользователя в Marzban."""
        try:
            logger.info(f"Снятие ограничений с пользователя {user_db.id}")
            user_data = await self.get_user(user_db)
            filtered_data = self.base_client.get_user_with_params(user_data, self.allowed_user_update_params)
            filtered_data["status"] = "active"
            await self.base_client.put(f"/user/{user_db.id}", filtered_data)
            logger.info(f"Ограничения сняты с пользователя {user_db.id}")
        except Exception as e:
            logger.error(f"Ошибка при снятии ограничений с пользователя {user_db.id}: {str(e)}", exc_info=True)
            raise

    async def reset_user(self, user_db: Users) -> None:
        """Сбрасывает трафик пользователя в Marzban."""
        # Предполагается, что этот эндпоинт сбрасывает статистику или лимиты
        try:
            logger.info(f"Сброс данных для пользователя {user_db.id}")
            response = await self.base_client.post(f"/user/{user_db.id}/reset", data={})
            if response.status_code == 200:
                logger.info(f"Данные пользователя {user_db.id} успешно сброшены")
            else:
                logger.error(f"Ошибка сброса данных для пользователя {user_db.id}. Статус: {response.status_code}, Ответ: {response.text}")
                raise ValueError(f"Failed to reset user. Status: {response.status_code}")
        except Exception as e:
            logger.error(f"Ошибка при сбросе данных пользователя {user_db.id}: {str(e)}", exc_info=True)
            raise

    async def create_user(self, user_db: Users) -> Dict[str, Any]:
        """Создает нового пользователя в Marzban."""
        try:
            logger.info(f"Начинаем создание пользователя {user_db.id} ({user_db.name()})")
            user_data = user_template.copy()
            user_data["username"] = str(user_db.id)
            user_data["note"] = user_db.name()
            
            logger.info(f"Отправляем POST запрос на создание пользователя с данными: {user_data}")
            response = await self.base_client.post("/user", user_data)
            
            if response.status_code == 422:
                error_detail = response.json()
                logger.error(f"Ошибка валидации при создании пользователя {user_db.id}. Ответ: {error_detail}")
                raise ValueError(f"Failed to create user: {error_detail}")
                
            if response.status_code != 201 and response.status_code != 200: # 200 иногда возвращается при успехе
                logger.error(f"Ошибка создания пользователя {user_db.id}. Статус: {response.status_code}, Ответ: {response.text}")
                raise ValueError(f"Failed to create user. Status: {response.status_code}")
            
            result = response.json()
            logger.info(f"Пользователь {user_db.id} успешно создан: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Исключение при создании пользователя {user_db.id}: {str(e)}", exc_info=True)
            raise

    async def delete_user(self, user_db: Users) -> bool:
        """Удаляет пользователя из Marzban."""
        try:
            username = str(user_db.id) # Используем ID как username
            logger.info(f"Попытка удаления пользователя {username} из Marzban")
            response = await self.base_client.delete(f"/user/{username}")

            # Считаем успешным удаление при статусах 200 или 204
            if response.status_code == 204 or response.status_code == 200:
                logger.info(f"Пользователь {username} успешно удален из Marzban (Статус: {response.status_code})")
                return True
            elif response.status_code == 404:
                logger.warning(f"Пользователь {username} не найден в Marzban при попытке удаления")
                # Считаем это успехом, так как пользователя и так нет
                return True
            else:
                logger.error(f"Ошибка при удалении пользователя {username} из Marzban. Статус: {response.status_code}, Ответ: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Исключение при удалении пользователя {user_db.id} из Marzban: {str(e)}", exc_info=True)
            return False

    async def get_online_count(self) -> int:
        """Получает количество активных пользователей через API Marzban."""
        # Этот метод неэффективен, если API не поддерживает фильтрацию по статусу
        # Лучше использовать get_users с фильтром, если возможно
        logger.warning("Метод get_online_count загружает всех пользователей, что может быть неэффективно.")
        try:
            response = await self.get_users(get_all=True, status="active")
            return response.get("total", 0)
        except Exception as e:
            logger.error(f"Ошибка получения количества онлайн пользователей: {str(e)}", exc_info=True)
            return 0 # Возвращаем 0 в случае ошибки

    async def get_subscription_url(self, user_db: Users) -> str:
        """Получает URL подписки пользователя, создавая его при необходимости."""
        try:
            logger.info(f"Получаем URL подписки для пользователя {user_db.id}")
            
            try:
                existing_user = await self.get_user(user_db)
                logger.info(f"Найден существующий пользователь: {existing_user['username']}")
                if "subscription_url" not in existing_user:
                     logger.error(f"В данных существующего пользователя {user_db.id} отсутствует subscription_url. Полные данные: {existing_user}")
                     raise ValueError("subscription_url not found in existing user data")
                # Возвращаем URL напрямую из ответа Marzban
                return existing_user["subscription_url"]
            except ValueError as e:
                if "User not found" in str(e):
                    logger.info(f"Пользователь {user_db.id} не найден, создаем нового")
                    new_user = await self.create_user(user_db)
                    logger.info(f"Создан новый пользователь {user_db.id}")
                    
                    if "subscription_url" not in new_user:
                        logger.error(f"В ответе на создание пользователя {user_db.id} отсутствует subscription_url. Полный ответ: {new_user}")
                        raise ValueError("subscription_url not found in new user response")
                        
                    # Возвращаем URL напрямую из ответа Marzban
                    return new_user["subscription_url"]
                else:
                    # Перебрасываем другие ValueError
                    raise
                
        except Exception as e:
            logger.error(f"Ошибка при получении URL подписки для пользователя {user_db.id}: {str(e)}", exc_info=True)
            raise

    async def get_users(self, offset: int = 0, limit: int = 100, username: Optional[str | List[str]] = None, 
                      search: Optional[str] = None, admin: Optional[str | List[str]] = None, 
                      status: Optional[str] = None, sort: Optional[str] = None, 
                      max_retries: int = 3, retry_delay: int = 2, get_all: bool = True) -> Dict[str, Any]:
        """
        Получает список пользователей из Marzban с пагинацией и параметрами.
        
        Args:
            offset: Смещение для пагинации.
            limit: Максимальное количество пользователей на страницу.
            username: Фильтр по имени пользователя (строка или список).
            search: Строка поиска.
            admin: Фильтр по администратору (строка или список).
            status: Фильтр по статусу.
            sort: Поле для сортировки.
            max_retries: Максимальное количество повторных попыток.
            retry_delay: Задержка между попытками (секунды).
            get_all: Если True, загружает все страницы результатов.
        
        Returns:
            Словарь с ключами "users" (список пользователей) и "total" (общее количество).
            В случае ошибки возвращает {"users": [], "total": 0}.
        """
        try:
            if get_all:
                all_users = []
                total = 0
                page_offset = offset
                
                # Получаем первую страницу для определения общего количества
                first_page = await self._get_users_page(
                    page_offset, limit, username, search, admin, status, sort, max_retries, retry_delay
                )
                
                if not first_page or "users" not in first_page:
                    logger.warning("Не удалось получить первую страницу пользователей при get_all=True")
                    return {"users": [], "total": 0}
                
                all_users.extend(first_page["users"])
                total = first_page.get("total", 0)
                page_limit = first_page.get("limit", limit) # Используем лимит из ответа, если он есть
                
                logger.info(f"get_users(get_all=True): Получена первая страница: {len(all_users)}/{total} пользователей")
                
                # Продолжаем загрузку, если пользователей больше, чем на первой странице
                while len(first_page["users"]) == page_limit and len(all_users) < total:
                    page_offset += page_limit
                    next_page = await self._get_users_page(
                        page_offset, page_limit, username, search, admin, status, sort, max_retries, retry_delay
                    )
                    
                    if not next_page or "users" not in next_page or not next_page["users"]:
                        logger.warning(f"get_users(get_all=True): Не удалось получить страницу {page_offset//page_limit + 1} или она пуста, останавливаем загрузку.")
                        break
                    
                    all_users.extend(next_page["users"])
                    logger.info(f"get_users(get_all=True): Загружено {len(all_users)}/{total} пользователей ({round(len(all_users)/total*100, 1)}%)")
                
                logger.info(f"get_users(get_all=True): Загрузка завершена. Всего {len(all_users)} пользователей.")
                return {"users": all_users, "total": total}
            
            # Если запрашивается только одна страница
            else:
                logger.info(f"get_users(get_all=False): Запрос одной страницы offset={offset}, limit={limit}")
                page_data = await self._get_users_page(
                    offset, limit, username, search, admin, status, sort, max_retries, retry_delay
                )
                return page_data if page_data else {"users": [], "total": 0}
                
        except Exception as e:
            logger.error(f"Критическая ошибка в get_users: {str(e)}", exc_info=True)
            return {"users": [], "total": 0}
            
    async def _get_users_page(self, offset: int, limit: int, username: Optional[str | List[str]] = None, 
                             search: Optional[str] = None, admin: Optional[str | List[str]] = None, 
                             status: Optional[str] = None, sort: Optional[str] = None, 
                             max_retries: int = 3, retry_delay: int = 2) -> Optional[Dict[str, Any]]:
        """
        Получает одну страницу пользователей с повторными попытками.
        
        Returns:
            Словарь с данными страницы или None в случае ошибки после всех попыток.
        """
        retry_count = 0
        path = "/users"
        
        while retry_count <= max_retries:
            try:
                params = {"offset": offset, "limit": limit}
                if username:
                    params["username"] = [str(u) for u in username] if isinstance(username, list) else [str(username)]
                if search: params["search"] = search
                if admin: 
                    params["admin"] = [str(a) for a in admin] if isinstance(admin, list) else [str(admin)]
                if status: params["status"] = status
                if sort: params["sort"] = sort
                
                response = await self.base_client.get(path, params=params)
                
                if response.status_code == 200:
                    data = response.json()
                    users_count = len(data.get("users", []))
                    total = data.get("total", users_count)
                    limit_resp = data.get("limit", limit)
                    logger.info(f"_get_users_page: Успешно получена страница: offset={offset}, limit={limit_resp}, count={users_count}, total={total}")
                    return data
                elif response.status_code == 524:  # Таймаут Cloudflare или Marzban
                    logger.warning(f"_get_users_page: Таймаут (524) при получении пользователей (offset={offset}). Попытка {retry_count+1}/{max_retries+1}")
                    # Обработка повторной попытки ниже
                else:
                    logger.error(f"_get_users_page: Ошибка {response.status_code} при получении пользователей (offset={offset}). Попытка {retry_count+1}/{max_retries+1}. Ответ: {response.text[:200]}")
                    # Обработка повторной попытки ниже

            except Exception as e:
                logger.warning(f"_get_users_page: Исключение при получении пользователей (offset={offset}): {str(e)}. Попытка {retry_count+1}/{max_retries+1}")
                # Обработка повторной попытки ниже

            # Логика повторных попыток
            retry_count += 1
            if retry_count <= max_retries:
                wait_time = retry_delay * retry_count # Экспоненциальная задержка?
                logger.info(f"_get_users_page: Повторная попытка через {wait_time} сек.")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"_get_users_page: Достигнуто максимальное количество попыток ({max_retries}) для offset={offset}")
                return None # Возвращаем None после всех неудачных попыток
        
        return None # Явно возвращаем None, если цикл завершился без успеха

# Шаблон для создания нового пользователя
user_template: Dict[str, Any] = {
    "note": "",
    "proxies": {"vless": {"flow": ""}}, # Пример, может потребовать настройки под вашу конфигурацию
    # "data_limit": 0, # Безлимит
    "expire": None, # Без срока действия
    # "data_limit_reset_strategy": "no_reset",
    "status": "active",
    "inbounds": { # Пример, должен соответствовать вашим настроенным inbounds
        "vmess": ["VMess TCP", "VMess Websocket"],
        "vless": ["VLESS TCP REALITY"],
        "trojan": ["Trojan Websocket TLS"],
        "shadowsocks": ["Shadowsocks TCP"],
    },
} 
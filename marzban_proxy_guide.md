# Реализация маскировки адреса Марзбана для пользователей

## Обзор реализации

Данная инструкция объясняет, как реализовать систему, которая скрывает прямой адрес панели управления Марзбана от пользователей VPN-сервиса. Вместо прямого доступа к панели Марзбана пользователи получают уникальные URL, которые проходят через ваш сервер приложения и затем перенаправляются на Марзбан.

### Ключевые компоненты:

1. **Генерация уникальных идентификаторов** для каждого пользователя
2. **Эндпоинт перенаправления** для обработки запросов подключения
3. **Клиент для взаимодействия с API Марзбана**
4. **Система хранения** идентификаторов пользователей
5. **Механизм логгирования** для отслеживания подключений

## Подробная реализация

### 1. Структура базы данных

В базе данных нужно хранить уникальный идентификатор для каждого пользователя. Пример модели пользователя:

```python
# users.py
from tortoise import fields, models
from zlib import crc32  # Для генерации хеша

def get_connect_url(user_id) -> str:
    """Генерирует уникальную ссылку для подключения на основе ID пользователя"""
    return (
        crc32(f"{user_id}connect") + crc32(f"connect {user_id}") + "cyberdog"
    )

class Users(models.Model):
    id = fields.BigIntField(primary_key=True)
    username = fields.CharField(max_length=100, null=True)
    full_name = fields.CharField(max_length=1000)
    # Другие поля пользователя
    
    # Поле для хранения уникальной ссылки подключения
    connect_url = fields.CharField(max_length=100, null=True)
    
    # Другие поля и методы
    
    @classmethod
    async def get_user(cls, telegram_user, referred_by: int = 0):
        # Создание или обновление пользователя
        user, is_new = await Users.update_or_create(
            id=telegram_user.id,
            defaults=dict(
                username=telegram_user.username,
                full_name=telegram_user.first_name + (f" {telegram_user.last_name}" if telegram_user.last_name else ""),
                connect_url=get_connect_url(telegram_user.id),  # Генерация ссылки при создании пользователя
                # Другие поля
            ),
        )
        # Дополнительная логика...
        return user
```

### 2. Настройки подключения к Марзбану

Создайте отдельный класс для хранения настроек подключения к Марзбану:

```python
# settings.py
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class MarzbanSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MARZBAN_")

    url: str  # URL Марзбан-сервера
    token: SecretStr  # Токен доступа к API Марзбана

# Инициализация настроек
marzban_settings = MarzbanSettings()
```

### 3. Клиент для работы с API Марзбана

Создайте класс-клиент для обращения к API Марзбана:

```python
# client.py
from httpx import AsyncClient
from cyberdog.settings import marzban_settings
from cyberdog.db.users import Users
from cyberdog.logger import get_logger

logger = get_logger("marzban_client")

class MarzbanClient:
    def __init__(self):
        logger.info(f"Инициализация клиента Marzban с URL: {marzban_settings.url}")
        
        if not marzban_settings.url:
            logger.error("URL Marzban не настроен")
            raise ValueError("MARZBAN_URL must be set in environment variables")
            
        if not marzban_settings.token:
            logger.error("Токен Marzban не настроен")
            raise ValueError("MARZBAN_TOKEN must be set in environment variables")
            
        try:
            self.client = AsyncClient(
                base_url=marzban_settings.url + "/api",
                headers={
                    "Authorization": "Bearer " + marzban_settings.token.get_secret_value()
                },
                timeout=120,
                proxies=[],
            )
            logger.info("Клиент Marzban успешно инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации клиента Marzban: {str(e)}", exc_info=True)
            raise
            
        # Список параметров пользователя Marzban
        self.params = [
            "status", "username", "note", "proxies",
            "data_limit", "expire", "data_limit_reset_strategy", "inbounds",
        ]
    
    # Базовые методы для работы с API
    
    async def get(self, path: str):
        try:
            # Логирование запросов с разным уровнем подробности
            if path.startswith("/users") or path.startswith("/nodes") or path.startswith("/inbounds"):
                logger.debug(f"GET запрос к {path}")
            else:
                logger.info(f"GET запрос к {path}")
                
            response = await self.client.get(path)
            
            # Логирование ответов
            if path.startswith("/users") or path.startswith("/nodes") or path.startswith("/inbounds"):
                logger.debug(f"Ответ на GET запрос к {path}: {response.status_code}")
            else:
                logger.info(f"Ответ на GET запрос к {path}: {response.status_code}")
                
            return response
        except Exception as e:
            logger.error(f"Ошибка GET запроса к {path}: {str(e)}", exc_info=True)
            raise
    
    async def post(self, path: str, data: dict):
        try:
            logger.info(f"POST запрос к {path} с данными: {data}")
            response = await self.client.post(path, json=data)
            logger.info(f"Ответ на POST запрос к {path}: {response.status_code}")
            return response
        except Exception as e:
            logger.error(f"Ошибка POST запроса к {path}: {str(e)}", exc_info=True)
            raise
    
    # Аналогично реализуйте методы PUT и DELETE
    
    # Метод для получения пользователя в Марзбане
    async def get_user(self, user_db: Users):
        try:
            response = await self.get(f"/user/{user_db.id}")
            
            if response.status_code == 404:
                raise ValueError("User not found in Marzban")
                
            if response.status_code != 200:
                logger.error(f"Ошибка получения пользователя {user_db.id}. Статус: {response.status_code}")
                raise ValueError(f"Failed to get user. Status: {response.status_code}")
                
            return response.json()
                
        except Exception as e:
            if not isinstance(e, ValueError) or "User not found in Marzban" not in str(e):
                logger.error(f"Исключение при получении пользователя {user_db.id}: {str(e)}")
            raise
    
    # Метод для создания пользователя в Марзбане
    async def create_user(self, user_db: Users):
        # Логика создания пользователя в Марзбане
        # ...
        
    # Ключевой метод для получения URL подписки
    async def get_url(self, user_db: Users) -> str:
        try:
            logger.info(f"Получаем URL для пользователя {user_db.id}")
            
            try:
                # Проверяем, есть ли уже пользователь в Марзбане
                existing_user = await self.get_user(user_db)
                logger.info(f"Найден существующий пользователь: {existing_user}")
                return marzban_settings.url + existing_user["subscription_url"]
            except ValueError:
                # Если пользователь не найден, создаем нового
                logger.info(f"Пользователь не найден, создаем нового")
                new_user = await self.create_user(user_db)
                logger.info(f"Создан новый пользователь, ответ: {new_user}")
                
                if "subscription_url" not in new_user:
                    logger.error(f"В ответе отсутствует subscription_url. Полный ответ: {new_user}")
                    raise ValueError("subscription_url not found in response")
                    
                return marzban_settings.url + new_user["subscription_url"]
                
        except Exception as e:
            logger.error(f"Ошибка при получении URL: {str(e)}", exc_info=True)
            raise
```

### 4. Эндпоинт перенаправления (основной механизм маскировки)

Создайте эндпоинт для обработки запросов подключения:

```python
# connect.py
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from httpx import AsyncClient
from cyberdog.logger import get_logger

from cyberdog.db.users import Users
from .client import MarzbanClient

logger = get_logger("marzban_connect")
marzban = MarzbanClient()
requests = AsyncClient()

router = APIRouter(prefix="/marzban", tags=["marzban"])

@router.get("/connect/{connection}")
async def connect(connection: str, request: Request):
    logger.info(f"Получен запрос на подключение с URL: {connection}")
    
    try:
        # Ищем пользователя по уникальной ссылке
        user = await Users.get(connect_url=connection)
        logger.info(f"Найден пользователь: {user.id}")
    except Exception as e:
        logger.error(f"Ошибка поиска пользователя: {str(e)}")
        raise HTTPException(status_code=404, detail="User not found")
        
    try:
        # Получаем URL подписки Марзбана для пользователя
        url = await marzban.get_url(user)
        logger.info(f"Получен URL Marzban для пользователя {user.id}")
        
        # Перенаправляем пользователя на URL подписки
        # Это ключевой момент: пользователь перенаправляется на Марзбан,
        # но не видит изначальный адрес в URL
        return RedirectResponse(url)
    except Exception as e:
        logger.error(f"Ошибка получения URL Marzban: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get Marzban URL")
```

### 5. Настройка логгирования

Настройте логирование для отслеживания операций:

```python
# logger.py (фрагмент)
import logging
import os
from datetime import datetime

# Настраиваем форматирование
formatter = logging.Formatter(
    '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Создаем фильтр для apscheduler и других системных сообщений
class ConnectionUpdateFilter(logging.Filter):
    def filter(self, record):
        # Проверяем, содержит ли сообщение информацию об обновлении времени подключения
        if "Обновлено время подключения для пользователя" in getattr(record, 'msg', ''):
            return False
        # Также фильтруем все DEBUG-сообщения от менее важных компонентов
        if record.levelno == logging.DEBUG and (record.name.startswith('tortoise.db_client') or record.name.startswith('websockets.client')):
            return False
        return True

def get_logger(name: str):
    """Получить логгер по имени"""
    return logging.getLogger(name)
```

## Инструкция по внедрению

1. **Настройка базы данных**:
   - Создайте модель пользователя с полем `connect_url`
   - Реализуйте функцию генерации уникальных идентификаторов
   - Внедрите логику сохранения идентификатора при регистрации пользователя

2. **Настройка клиента Марзбана**:
   - Создайте класс настроек с URL и токеном API
   - Реализуйте класс-клиент с методами для работы с API Марзбана
   - Реализуйте методы получения и создания пользователей
   - Внедрите метод `get_url`, который возвращает URL подписки

3. **Реализация эндпоинта перенаправления**:
   - Создайте эндпоинт `/connect/{connection}`, где `connection` - это уникальный идентификатор
   - Реализуйте логику поиска пользователя по идентификатору
   - Используйте клиент Марзбана для получения URL подписки
   - Перенаправьте пользователя на полученный URL

4. **Интеграция с UI**:
   - В вашем интерфейсе вместо прямых ссылок на Марзбан используйте ссылки вида 
     `https://ваш_домен/marzban/connect/{connect_url}`
   - При отображении QR-кодов или инструкций по подключению используйте эти ссылки

5. **Настройка логгирования**:
   - Реализуйте подробное логирование всех операций для отладки

## Пример API-маршрутов для Телеграм-бота

```python
# Пример обработчика в Телеграм-боте для генерации ссылки
@router.message(Command("get_link"))
async def get_link_handler(message: Message):
    user_id = message.from_user.id
    user = await Users.get(id=user_id)
    
    if not user.connect_url:
        # Если по какой-то причине у пользователя нет connect_url, генерируем его
        user.connect_url = get_connect_url(user_id)
        await user.save()
    
    # Формируем полный URL для подключения
    connect_link = f"{your_server_url}/marzban/connect/{user.connect_url}"
    
    # Отправляем пользователю ссылку
    await message.answer(f"Ваша ссылка для подключения: {connect_link}")
```

## Дополнительные рекомендации

1. **Безопасность**:
   - Не храните токены API Марзбана в открытом виде, используйте переменные окружения
   - Валидируйте все входящие запросы на эндпоинте подключения
   - Ограничивайте количество запросов с одного IP-адреса

2. **Производительность**:
   - Кэшируйте результаты запросов к API Марзбана
   - Используйте асинхронный HTTP-клиент для работы с API

3. **Отказоустойчивость**:
   - Реализуйте механизм повторных попыток для запросов к API
   - Обрабатывайте случаи недоступности API Марзбана

4. **Мониторинг**:
   - Логируйте все подключения и операции с системой
   - Настройте алерты на ошибки подключения

## Пример полной схемы работы

1. Пользователь регистрируется в системе, ему генерируется `connect_url`
2. Пользователь получает ссылку вида `https://ваш_домен/marzban/connect/{connect_url}`
3. При переходе по ссылке ваш сервер:
   - Находит пользователя по `connect_url`
   - Запрашивает у Марзбана URL подписки
   - Перенаправляет пользователя на полученный URL
4. Пользователь получает конфигурацию VPN, не видя прямой адрес Марзбана

Все эти шаги выполняются прозрачно для пользователя, и он никогда не видит реальный адрес панели Марзбана. 
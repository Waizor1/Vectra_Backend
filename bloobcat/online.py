import asyncio
from datetime import datetime

import websockets # type: ignore
from pytz import UTC

from bloobcat.db.users import Users
from bloobcat.settings import marzban_settings
from bloobcat.routes.marzban.client import MarzbanClient
from bloobcat.logger import get_logger
from bloobcat.routes.marzban.catcher import process_user

logger = get_logger("bloobcat")
marzban = MarzbanClient()

# online_ids = set()
# online_ids_lock = asyncio.Lock()


async def listen_ws(url: str):
    url_log = url.split("token=")[0]
    while True:
        try:
            logger.info(f"Подключение к {url_log}...")
            async with websockets.connect(url) as websocket:
                logger.info(f"Подключено к {url_log}")
                async for message in websocket:
                    await process_message(message)
        except Exception as e:
            logger.error(f"Ошибка подключения к {url_log}: {e}")
        logger.info(f"Переподключение к {url_log} через 5 секунд...")
        await asyncio.sleep(5)


async def process_message(message: str):
    if "email:" in message:
        parts = message.split("email:")
        if len(parts) > 1:
            email_field = parts[1].strip().split()[0]
            if "." in email_field:
                try:
                    _, id_value = email_field.split(".", 1)
                    # async with online_ids_lock:
                    #     online_ids.add(id_value)
                    user = await Users.get_or_none(id=id_value)
                    if user:
                        was_registered = user.is_registered
                        user.connected_at = datetime.now(UTC)
                        await user.save()
                        logger.debug(f"Обновлено время подключения для пользователя {id_value}")
                        
                        # Если пользователь не зарегистрирован, вызываем process_user напрямую
                        if not was_registered:
                            try:
                                logger.info(f"Прямой вызов process_user для пользователя {id_value}")
                                asyncio.create_task(process_user(user))
                            except Exception as e:
                                logger.error(f"Ошибка при прямом вызове process_user: {e}")
                except Exception as e:
                    logger.error(f"Ошибка обработки поля email '{email_field}': {e}")
            else:
                logger.warning(f"В поле email не найдена точка: {email_field}")


async def online_worker_tasks():
    try:
        # Получаем ноды через под-клиент infra
        nodes = await marzban.infra.get_nodes()
        tasks = []
        
        # Добавляем задачу для основной панели (core)
        core_url = f"wss://{marzban_settings.url.replace('https://', '')}/api/core/logs?interval=1&token={marzban_settings.token.get_secret_value()}"
        tasks.append(asyncio.create_task(listen_ws(core_url)))
        logger.info("Добавлена задача мониторинга для основной панели (core)")
        
        # Добавляем задачи для нод
        if nodes:
            for node in nodes:
                if node.get("status") == "connected":
                    node_id = node.get("id")
                    url = f"wss://{marzban_settings.url.replace('https://', '')}/api/node/{node_id}/logs?interval=1&token={marzban_settings.token.get_secret_value()}"
                    tasks.append(asyncio.create_task(listen_ws(url)))
                    logger.info(f"Добавлена задача мониторинга для ноды {node_id}")
        else:
            logger.info("Активные ноды не найдены")
            
        return tasks
    except Exception as e:
        logger.error(f"Ошибка при инициализации задач мониторинга: {e}")
        return []


# if __name__ == "__main__":
#     try:
#         asyncio.run(main())
#     except KeyboardInterrupt:
#         print("Завершение работы скрипта.")

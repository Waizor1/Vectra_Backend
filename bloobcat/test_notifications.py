"""
Модуль для тестирования системы уведомлений
"""
import asyncio
import os
from typing import List, Dict, Any
import pytest
from bloobcat.db.users import Users
from bloobcat.bot.notifications.trial.extended import notify_trial_extended
from bloobcat.logger import get_logger

logger = get_logger("test_notifications")

if not os.getenv("RUN_NOTIFICATION_TESTS"):
    pytest.skip("RUN_NOTIFICATION_TESTS is not set", allow_module_level=True)


@pytest.fixture
def user_id() -> int:
    raw = os.getenv("TEST_NOTIFICATION_USER_ID")
    if not raw:
        pytest.skip("TEST_NOTIFICATION_USER_ID is not set")
    return int(raw)


@pytest.fixture
def user_ids() -> List[int]:
    raw = os.getenv("TEST_NOTIFICATION_USER_IDS")
    if not raw:
        pytest.skip("TEST_NOTIFICATION_USER_IDS is not set")
    values = [int(x.strip()) for x in raw.split(",") if x.strip()]
    if not values:
        pytest.skip("TEST_NOTIFICATION_USER_IDS is empty")
    return values


@pytest.mark.asyncio
async def test_trial_extension_notification(user_id: int) -> Dict[str, Any]:
    """
    Функция для ручного тестирования отправки уведомления о продлении trial.
    Возвращает детальную информацию о результате.
    """
    result = {
        "user_id": user_id,
        "success": False,
        "error": None,
        "details": {},
        "timing": {}
    }
    
    start_time = asyncio.get_event_loop().time()
    
    try:
        # Получаем пользователя
        user = await Users.get_or_none(id=user_id)
        if not user:
            result["error"] = "User not found"
            return result
            
        result["details"]["user_exists"] = True
        result["details"]["user_full_name"] = user.full_name
        result["details"]["user_language"] = getattr(user, 'language_code', 'unknown')
        result["details"]["is_trial"] = user.is_trial
        result["details"]["expired_at"] = str(user.expired_at) if user.expired_at else None
        
        # Пытаемся отправить уведомление
        logger.info(f"Testing trial extension notification for user {user_id}")
        
        notification_start = asyncio.get_event_loop().time()
        await asyncio.wait_for(
            notify_trial_extended(user, 5),
            timeout=60.0  # 1 минута для тестирования
        )
        notification_end = asyncio.get_event_loop().time()
        
        result["success"] = True
        result["timing"]["notification_duration"] = notification_end - notification_start
        result["details"]["message"] = "Notification sent successfully"
        
        logger.info(f"Test trial extension notification for user {user_id} completed successfully in {notification_end - notification_start:.2f}s")
        
    except asyncio.TimeoutError:
        result["error"] = "Timeout after 60 seconds"
        result["details"]["message"] = "Notification sending timed out"
    except Exception as e:
        result["error"] = str(e)
        result["details"]["message"] = f"Unexpected error: {e}"
        logger.error(f"Test trial extension notification for user {user_id} failed: {e}", exc_info=True)
    
    end_time = asyncio.get_event_loop().time()
    result["timing"]["total_duration"] = end_time - start_time
    
    return result

@pytest.mark.asyncio
async def test_multiple_trial_notifications(user_ids: List[int], concurrent: bool = False) -> Dict[str, Any]:
    """
    Тестирует отправку уведомлений нескольким пользователям.
    
    Args:
        user_ids: Список ID пользователей
        concurrent: Если True, отправляет уведомления параллельно
    
    Returns:
        Словарь с результатами тестирования
    """
    results = {
        "total_users": len(user_ids),
        "successful": 0,
        "failed": 0,
        "concurrent": concurrent,
        "user_results": {},
        "timing": {}
    }
    
    start_time = asyncio.get_event_loop().time()
    
    if concurrent:
        # Параллельная отправка
        logger.info(f"Testing trial notifications for {len(user_ids)} users (concurrent)")
        tasks = [test_trial_extension_notification(user_id) for user_id in user_ids]
        user_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, user_id in enumerate(user_ids):
            if isinstance(user_results[i], Exception):
                results["user_results"][user_id] = {
                    "success": False,
                    "error": str(user_results[i])
                }
                results["failed"] += 1
            else:
                results["user_results"][user_id] = user_results[i]
                if user_results[i]["success"]:
                    results["successful"] += 1
                else:
                    results["failed"] += 1
    else:
        # Последовательная отправка
        logger.info(f"Testing trial notifications for {len(user_ids)} users (sequential)")
        for user_id in user_ids:
            try:
                user_result = await test_trial_extension_notification(user_id)
                results["user_results"][user_id] = user_result
                if user_result["success"]:
                    results["successful"] += 1
                else:
                    results["failed"] += 1
                    
                # Небольшая задержка между отправками
                await asyncio.sleep(0.5)
            except Exception as e:
                results["user_results"][user_id] = {
                    "success": False,
                    "error": str(e)
                }
                results["failed"] += 1
    
    end_time = asyncio.get_event_loop().time()
    results["timing"]["total_duration"] = end_time - start_time
    results["timing"]["average_per_user"] = results["timing"]["total_duration"] / len(user_ids)
    
    logger.info(f"Test completed: {results['successful']}/{results['total_users']} successful, took {results['timing']['total_duration']:.2f}s")
    
    return results 
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from bloobcat.logger import get_logger

logger = get_logger("telegram_error_handler")
MOSCOW = ZoneInfo("Europe/Moscow")

async def handle_telegram_forbidden_error(user_id: int, error: TelegramForbiddenError) -> bool:
    """
    Централизованная обработка блокировки пользователя ботом.
    
    Args:
        user_id: ID пользователя
        error: Ошибка TelegramForbiddenError
        
    Returns:
        bool: True если пользователь был помечен как заблокированный, False если уже был заблокирован
    """
    try:
        from bloobcat.db.users import Users
        from bloobcat.scheduler import cancel_user_tasks
        
        user = await Users.get_or_none(id=user_id)
        if not user:
            logger.warning(f"User {user_id} not found when handling Telegram forbidden error")
            return False
            
        # Если пользователь уже помечен как заблокированный, не дублируем
        if user.is_blocked:
            logger.debug(f"User {user_id} already marked as blocked")
            return False
            
        # Помечаем пользователя как заблокированного
        user.is_blocked = True
        user.blocked_at = datetime.now(MOSCOW)
        user.failed_message_count += 1
        user.last_failed_message_at = datetime.now(MOSCOW)
        
        await user.save()
        
        # Отменяем все запланированные задачи для заблокированного пользователя
        cancel_user_tasks(user_id)
        
        logger.warning(f"User {user_id} blocked the bot, marked for cleanup. Error: {error}")
        return True
        
    except Exception as e:
        logger.error(f"Error handling Telegram forbidden error for user {user_id}: {e}")
        return False

async def handle_telegram_bad_request(user_id: int, error: TelegramBadRequest) -> bool:
    """
    Обработка ошибок TelegramBadRequest (например, "chat not found").
    
    Args:
        user_id: ID пользователя
        error: Ошибка TelegramBadRequest
        
    Returns:
        bool: True если пользователь был помечен как заблокированный, False в противном случае
    """
    try:
        error_text = str(error).lower()
        
        # "chat not found" обычно означает, что пользователь удалил аккаунт
        if "chat not found" in error_text:
            from bloobcat.db.users import Users
            from bloobcat.scheduler import cancel_user_tasks
            
            user = await Users.get_or_none(id=user_id)
            if not user:
                logger.warning(f"User {user_id} not found when handling chat not found error")
                return False
                
            if not user.is_blocked:
                user.is_blocked = True
                user.blocked_at = datetime.now(MOSCOW)
                user.failed_message_count += 1
                user.last_failed_message_at = datetime.now(MOSCOW)
                
                await user.save()
                cancel_user_tasks(user_id)
                
                logger.warning(f"User {user_id} chat not found (likely deleted account), marked for cleanup")
                return True
        else:
            # Для других TelegramBadRequest просто логируем
            logger.debug(f"TelegramBadRequest for user {user_id}: {error}")
            
        return False
        
    except Exception as e:
        logger.error(f"Error handling TelegramBadRequest for user {user_id}: {e}")
        return False

async def handle_telegram_error_with_retry(user_id: int, error: Exception) -> bool:
    """
    Обработка общих ошибок Telegram с увеличением счетчика неудач.
    
    Args:
        user_id: ID пользователя
        error: Любая ошибка при отправке сообщения
        
    Returns:
        bool: True если пользователь был помечен как заблокированный из-за множественных ошибок
    """
    try:
        from bloobcat.db.users import Users
        from bloobcat.scheduler import cancel_user_tasks
        from bloobcat.settings import app_settings
        
        user = await Users.get_or_none(id=user_id)
        if not user or user.is_blocked:
            return False
            
        # Увеличиваем счетчик неудач
        user.failed_message_count += 1
        user.last_failed_message_at = datetime.now(MOSCOW)
        
        # Если превышен лимит неудач, помечаем как заблокированного
        max_failed_attempts = getattr(app_settings, 'blocked_user_max_failed_attempts', 5)
        if user.failed_message_count >= max_failed_attempts:
            user.is_blocked = True
            user.blocked_at = datetime.now(MOSCOW)
            
            await user.save()
            cancel_user_tasks(user_id)
            
            logger.warning(f"User {user_id} marked as blocked after {user.failed_message_count} failed attempts. Last error: {error}")
            return True
        else:
            await user.save()
            logger.debug(f"User {user_id} failed message count: {user.failed_message_count}/{max_failed_attempts}")
            return False
            
    except Exception as e:
        logger.error(f"Error handling general Telegram error for user {user_id}: {e}")
        return False

async def reset_user_failed_count(user_id: int) -> bool:
    """
    Сброс счетчика неудачных попыток при успешной отправке сообщения.
    
    Args:
        user_id: ID пользователя
        
    Returns:
        bool: True если счетчик был сброшен
    """
    try:
        from bloobcat.db.users import Users
        
        user = await Users.get_or_none(id=user_id)
        if not user or user.failed_message_count == 0:
            return False
            
        user.failed_message_count = 0
        user.last_failed_message_at = None
        await user.save()
        
        logger.debug(f"Reset failed message count for user {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error resetting failed count for user {user_id}: {e}")
        return False 
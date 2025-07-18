import asyncio
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
import random

logger = get_logger("notifications.trial.extend")

async def notify_trial_extended(user, days: int):
    """
    Уведомляет пользователя о продлении пробного периода.
    ОПТИМИЗИРОВАНО: Правильная обработка Telegram API ошибок 429 с retry_after.
    """
    max_retries = 3
    base_delay = 1.0  # базовая задержка для exponential backoff
    
    for attempt in range(max_retries):
        try:
            logger.debug(f"[{user.id}] Starting notify_trial_extended for {days} days (attempt {attempt + 1}/{max_retries})")
            
            # Получаем локаль пользователя
            locale = get_user_locale(user)
            
            # Формируем сообщение
            if locale == "en":
                message_text = f"🎉 Great news! Your free trial has been extended for {days} more day{'s' if days != 1 else ''}!\n\nEnjoy your extended access and feel free to explore all features."
                button_text = "Open App"
            else:
                message_text = f"🎉 Отличные новости! Ваш бесплатный период продлён ещё на {days} дн{'ей' if days != 1 else 'ь'}!\n\nНаслаждайтесь расширенным доступом и изучайте все возможности."
                button_text = "Открыть приложение"
            
            # Создаём кнопку с таймаутом (оптимизировано до 5 секунд)
            try:
                keyboard = await asyncio.wait_for(
                    webapp_inline_button(text=button_text, url="/second"),
                    timeout=5.0
                )
                logger.debug(f"[{user.id}] Keyboard created successfully")
            except asyncio.TimeoutError:
                logger.warning(f"[{user.id}] Keyboard creation timed out, sending without keyboard")
                keyboard = None
            except Exception as e:
                logger.warning(f"[{user.id}] Failed to create keyboard: {e}, sending without keyboard")
                keyboard = None
            
            # Отправляем сообщение с оптимизированным таймаутом (15 секунд)
            try:
                await asyncio.wait_for(
                    bot.send_message(
                        chat_id=user.id,
                        text=message_text,
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    ),
                    timeout=15.0
                )
                
                logger.info(f"[{user.id}] Trial extension notification sent successfully")
                return True
                
            except TelegramRetryAfter as e:
                # ОПТИМИЗИРОВАНО: Правильная обработка retry_after из Telegram API
                retry_after = e.retry_after
                logger.warning(f"[{user.id}] Telegram rate limit hit, retry after {retry_after}s (attempt {attempt + 1})")
                
                if attempt < max_retries - 1:  # не последняя попытка
                    # Добавляем jitter для распределения нагрузки
                    jitter = random.uniform(0.1, 0.5)
                    wait_time = retry_after + jitter
                    logger.debug(f"[{user.id}] Waiting {wait_time:.1f}s before retry")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"[{user.id}] Exhausted retries after Telegram rate limits")
                    raise
                    
            except TelegramBadRequest as e:
                logger.error(f"[{user.id}] Bad request error: {e}")
                if "chat not found" in str(e).lower():
                    logger.debug(f"[{user.id}] User chat not found, likely deleted account")
                return False
                
            except TelegramForbiddenError as e:
                logger.warning(f"[{user.id}] User blocked the bot: {e}")
                return False
                
            except asyncio.TimeoutError:
                logger.debug(f"[{user.id}] Message sending timed out after 15s (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    # Exponential backoff для таймаутов
                    delay = base_delay * (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.debug(f"[{user.id}] Retrying after {delay:.1f}s timeout")
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise
                    
            except Exception as e:
                logger.error(f"[{user.id}] Unexpected error sending message: {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.debug(f"[{user.id}] Retrying after {delay:.1f}s due to error")
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise
                    
        except Exception as e:
            logger.error(f"[{user.id}] Critical error in notify_trial_extended: {e}")
            return False
    
    # Если мы дошли сюда, все попытки исчерпаны
    logger.error(f"[{user.id}] Failed to send trial extension notification after {max_retries} attempts")
    return False 
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bloobcat.logger import get_logger
from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.connections import Connections
from tortoise.expressions import Q

# Импорты уведомлений перенесены внутрь функции для избежания циклического импорта

logger = get_logger("schedules.trials")

async def check_trial_users():
    """
    Проверяет пользователей с пробным периодом и тех, кто не взял пробную подписку:
    - Уведомляет о завершении пробного периода
    - Уведомляет пользователей, которые не взяли пробную подписку через 2 часа и через сутки
    """
    try:
        logger.info("Начало проверки пользователей с пробным периодом")
        
        # Импортируем функции уведомлений здесь, чтобы избежать циклического импорта
        from bloobcat.bot.notifications.trial.end import notify_trial_ended
        from bloobcat.bot.notifications.trial.no_trial import notify_no_trial_taken
        from bloobcat.bot.notifications.trial.extended import notify_trial_extended
        
        # Текущее время в часовой зоне MSK
        moscow_tz = ZoneInfo("Europe/Moscow")
        now = datetime.now(moscow_tz)
        today = now.date()
        
        # Добавляем механизм повторных попыток для всех запросов к базе данных
        max_retry_time = 30  # максимальное время повторных попыток в секундах
        retry_interval = 3   # интервал между повторными попытками в секундах
        
        # Функция для выполнения запросов к базе данных с повторными попытками
        async def db_request_with_retry(request_func, description):
            start_retry_time = datetime.now()
            retry_attempt = 0
            result = None
            
            while result is None:
                try:
                    result = await request_func()
                    return result
                except Exception as e:
                    retry_attempt += 1
                    elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                    
                    if elapsed_retry_time > max_retry_time:
                        logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек) для '{description}'. Последняя ошибка: {str(e)}")
                        raise
                    
                    logger.warning(f"Ошибка при выполнении '{description}' (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек. Прошло {elapsed_retry_time:.1f} из {max_retry_time} сек.")
                    await asyncio.sleep(retry_interval)
            
            # Если result все еще None после всех попыток
            logger.error(f"Не удалось выполнить '{description}' после всех попыток")
            return None
        
        # Проверяем пользователей с истекающим пробным периодом в ближайшие 12 часов
        twelve_hours_later = now + timedelta(hours=12)
        expiring_trial_users = await db_request_with_retry(
            lambda: Users.filter(
                is_registered=True,
                is_trial=True,
                expired_at__gt=today,
                expired_at__lte=twelve_hours_later.date(),
                connected_at__isnull=True  # Добавляем проверку на отсутствие подключений
            ),
            "получение пользователей с истекающим пробным периодом без подключений"
        )
        
        if expiring_trial_users is None:
            expiring_trial_users = []
        
        logger.info(f"Найдено {len(expiring_trial_users)} пользователей с истекающим пробным периодом без подключений")
        
        # Продлеваем триал для пользователей без подключений
        for user in expiring_trial_users:
            try:
                # Продлеваем триал на 5 дней
                user.expired_at = user.expired_at + timedelta(days=5)
                await user.save()
                
                # Отправляем уведомление о продлении
                await notify_trial_extended(user, 5)
                logger.info(f"Триал продлен на 5 дней для пользователя {user.id} без подключений")
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id} с истекающим пробным периодом: {str(e)}")
                continue
        
        # 1. Проверяем пользователей с истекшим пробным периодом
        # Получаем пользователей с пробным периодом, который истек сегодня
        trial_ended_users = await db_request_with_retry(
            lambda: Users.filter(
                is_registered=True,
                is_trial=True,
                expired_at=today
            ),
            "получение пользователей с истекшим пробным периодом"
        )
        
        if trial_ended_users is None:
            trial_ended_users = []
        
        logger.info(f"Найдено {len(trial_ended_users)} пользователей с истекшим пробным периодом")
        
        # Отправляем уведомления пользователям с истекшим пробным периодом
        for user in trial_ended_users:
            try:
                logger.info(f"Отправка уведомления о завершении пробного периода пользователю {user.id}")
                await notify_trial_ended(user)
                
                # Обновляем статус пользователя
                user.is_trial = False
                await user.save()
                logger.info(f"Статус пробного периода обновлен для пользователя {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id} с истекшим пробным периодом: {str(e)}")
                continue
        
        # 2. Проверяем пользователей, которые не взяли пробную подписку
        two_hours_ago = now - timedelta(hours=2)
        one_day_ago = now - timedelta(days=1)
        three_days_ago = now - timedelta(days=3)
        
        # Получаем список пользователей, у которых есть платежи
        users_with_payments = await db_request_with_retry(
            lambda: ProcessedPayments.filter(
                status="succeeded"
            ).values_list("user_id", flat=True),
            "получение пользователей с платежами"
        )
        
        if users_with_payments is None:
            users_with_payments = []
        
        logger.info(f"Найдено {len(users_with_payments)} пользователей с платежами")
        
        # Пользователи, которые зарегистрировались 2 часа назад
        users_2h = await db_request_with_retry(
            lambda: Users.filter(
                is_trial=True,
                connected_at__isnull=True,
                created_at__lte=two_hours_ago,
                created_at__gt=one_day_ago
            ).exclude(
                id__in=users_with_payments
            ),
            "получение пользователей, не взявших пробную подписку за 2 часа"
        )
        
        if users_2h is None:
            users_2h = []
        
        logger.info(f"Найдено {len(users_2h)} пользователей, не взявших пробную подписку за 2 часа")
        
        # Отправляем уведомления пользователям, не взявшим пробную подписку за 2 часа
        for user in users_2h:
            try:
                # Проверяем, что пользователь еще не получал это уведомление
                if not user.notification_2h_sent:
                    logger.info(f"Отправка уведомления пользователю {user.id}, не взявшему пробную подписку за 2 часа")
                    await notify_no_trial_taken(user, 2)
                    
                    # Обновляем статус уведомления
                    user.notification_2h_sent = True
                    await user.save()
                    logger.info(f"Статус уведомления обновлен для пользователя {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id}, не взявшего пробную подписку за 2 часа: {str(e)}")
                continue
        
        # Пользователи, которые зарегистрировались сутки назад
        users_24h = await db_request_with_retry(
            lambda: Users.filter(
                is_trial=True,
                connected_at__isnull=True,
                created_at__lte=one_day_ago,
                created_at__gt=three_days_ago
            ).exclude(
                id__in=users_with_payments
            ),
            "получение пользователей, не взявших пробную подписку за сутки"
        )
        
        if users_24h is None:
            users_24h = []
        
        logger.info(f"Найдено {len(users_24h)} пользователей, не взявших пробную подписку за сутки")
        
        # Отправляем уведомления пользователям, не взявшим пробную подписку за сутки
        for user in users_24h:
            try:
                # Проверяем, что пользователь еще не получал это уведомление
                if not user.notification_24h_sent:
                    logger.info(f"Отправка уведомления пользователю {user.id}, не взявшему пробную подписку за сутки")
                    await notify_no_trial_taken(user, 24)
                    
                    # Обновляем статус уведомления
                    user.notification_24h_sent = True
                    await user.save()
                    logger.info(f"Статус уведомления обновлен для пользователя {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id}, не взявшего пробную подписку за сутки: {str(e)}")
                continue
        
        logger.info("Проверка пользователей с пробным периодом успешно завершена")
    except Exception as e:
        logger.error(f"Ошибка при проверке пользователей с пробным периодом: {str(e)}") 
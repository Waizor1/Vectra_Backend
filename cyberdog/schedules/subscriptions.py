from datetime import datetime
from cyberdog.logger import get_logger

from cyberdog.db.users import Users
from cyberdog.routes.payment import create_auto_payment
# Импорты уведомлений перенесены внутрь функции для избежания циклического импорта

logger = get_logger("schedules.subscriptions")

async def check_subscriptions():
    """
    Проверяет подписки пользователей:
    - Уведомляет о предстоящем списании за 1-2 дня (для пользователей с автопродлением)
    - Уведомляет о скором истечении подписки (для пользователей без автопродления)
    - Создает автоплатеж в день истечения подписки
    """
    try:
        logger.info("Начало проверки подписок пользователей")
        
        # Импортируем функции уведомлений здесь, чтобы избежать циклического импорта
        from cyberdog.bot.notifications.user import notify_auto_payment, notify_expiring_subscription
        
        # Получаем всех пользователей для отладки
        all_users = await Users.all()
        logger.info(f"Всего пользователей в базе: {len(all_users)}")
        
        # Логируем информацию о пользователе с ID 367192315
        user_367192315 = await Users.get_or_none(id=367192315)
        if user_367192315:
            logger.info(
                f"Информация о пользователе 367192315: "
                f"is_registered={user_367192315.is_registered}, "
                f"is_subscribed={user_367192315.is_subscribed}, "
                f"renew_id={user_367192315.renew_id}, "
                f"expired_at={user_367192315.expired_at}, "
                f"days_remaining={(user_367192315.expired_at - datetime.now().date()).days if user_367192315.expired_at else 'None'}"
            )
        
        # Получаем пользователей с автопродлением для проверки подписок
        users_with_auto_renewal = await Users.filter(
            is_subscribed=True,
            renew_id__not_isnull=True,
            expired_at__not_isnull=True
        )
        
        logger.info(f"Найдено {len(users_with_auto_renewal)} пользователей с автопродлением")
        
        # Проверяем, есть ли пользователь 367192315 в списке для проверки
        if user_367192315 and user_367192315 not in users_with_auto_renewal and user_367192315.is_subscribed:
            logger.info(
                f"Пользователь 367192315 не входит в список для проверки автопродления. "
                f"Причины: is_subscribed={user_367192315.is_subscribed}, "
                f"renew_id is null={user_367192315.renew_id is None}, "
                f"expired_at is null={user_367192315.expired_at is None}"
            )
        
        # Обрабатываем пользователей с автопродлением
        for user in users_with_auto_renewal:
            try:
                days_remaining = (user.expired_at - datetime.now().date()).days
                # Логируем информацию о подписке пользователя
                logger.info(f"Проверка подписки для пользователя {user.id} с автопродлением: истекает через {days_remaining} дней")
                
                # Проверяем, что подписка еще не истекла (для уведомлений)
                if days_remaining < 0:
                    logger.info(f"Подписка пользователя {user.id} уже истекла, уведомление не отправляется")
                    continue
                
                if 0 < days_remaining < 3:
                    # Только уведомляем пользователя о предстоящем списании
                    logger.info(f"Отправка уведомления о предстоящем списании пользователю {user.id}")
                    # await notify_auto_payment(user) # Закомментировано по запросу
                elif days_remaining == 0:
                    # Создаем автоплатеж в день истечения
                    logger.info(f"Создание автоплатежа для пользователя {user.id}")
                    result = await create_auto_payment(user)
                    if result:
                        logger.info(f"Автоплатеж для пользователя {user.id} успешно создан")
                    else:
                        logger.error(f"Не удалось создать автоплатеж для пользователя {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id} с автопродлением: {str(e)}")
                continue
        
        # Получаем пользователей без автопродления, но с активной подпиской
        users_without_auto_renewal = await Users.filter(
            is_registered=True,
            expired_at__not_isnull=True
        ).filter(
            is_subscribed=False
        ).all()
        
        logger.info(f"Найдено {len(users_without_auto_renewal)} пользователей без автопродления")
        
        # Проверяем, есть ли пользователи с is_subscribed=False, но с renew_id
        for user in users_without_auto_renewal:
            if user.renew_id:
                logger.warning(f"Пользователь {user.id} имеет renew_id={user.renew_id}, но is_subscribed=False. Возможно, нужно исправить данные.")
        
        # Обрабатываем пользователей без автопродления
        for user in users_without_auto_renewal:
            try:
                days_remaining = (user.expired_at - datetime.now().date()).days
                # Логируем информацию о подписке пользователя
                logger.info(f"Проверка подписки для пользователя {user.id} без автопродления: истекает через {days_remaining} дней")
                
                # Проверяем, что подписка еще не истекла
                if days_remaining <= 0:
                    logger.info(f"Подписка пользователя {user.id} уже истекла или истекает сегодня, уведомление не отправляется")
                    continue
                
                # Отправляем уведомления за 1, 2 и 3 дня до истечения подписки
                if days_remaining in [1, 2, 3]:
                    # Уведомляем пользователя о скором истечении подписки
                    logger.info(f"Отправка уведомления о скором истечении подписки пользователю {user.id} (осталось {days_remaining} дн.)")
                    await notify_expiring_subscription(user)
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id} без автопродления: {str(e)}")
                continue
        
        logger.info("Проверка подписок пользователей успешно завершена")
    except Exception as e:
        logger.error(f"Ошибка при проверке подписок пользователей: {str(e)}") 
from datetime import datetime
from bloobcat.logger import get_logger

from bloobcat.db.users import Users
from bloobcat.routes.payment import create_auto_payment
from bloobcat.bot.notifications.subscription.expiration import notify_auto_payment, notify_expiring_subscription
from bloobcat.bot.notifications.subscription.key import on_disabled
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
        logger.debug("Начало проверки подписок пользователей")
        
        # Получаем всех пользователей для отладки
        all_users = await Users.all()
        logger.debug(f"Всего пользователей в базе: {len(all_users)}")
        
        # Получаем пользователей с автопродлением для проверки подписок
        users_with_auto_renewal = await Users.filter(
            is_subscribed=True,
            renew_id__not_isnull=True,
            expired_at__not_isnull=True,
            is_trial=False
        )
        
        logger.debug(f"Найдено {len(users_with_auto_renewal)} пользователей с автопродлением")
        
        # Обрабатываем пользователей с автопродлением
        for user in users_with_auto_renewal:
            try:
                days_remaining = (user.expired_at - datetime.now().date()).days
                
                if days_remaining < 0:
                    logger.debug(f"Подписка пользователя {user.id} уже истекла")
                    continue
                
                if 0 < days_remaining < 3:
                    logger.debug(f"Отправка уведомления о предстоящем списании пользователю {user.id}")
                    # await notify_auto_payment(user) # Закомментировано по запросу
                elif days_remaining == 0:
                    logger.debug(f"Создание автоплатежа для пользователя {user.id}")
                    result = await create_auto_payment(user)
                    if not result:
                        logger.error(f"Не удалось создать автоплатеж для пользователя {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id} с автопродлением: {str(e)}")
                continue
        
        # Получаем пользователей без автопродления, но с активной подпиской
        users_without_auto_renewal = await Users.filter(
            is_registered=True,
            expired_at__not_isnull=True,
            is_subscribed=False,
            is_trial=False
        ).all()
        
        logger.debug(f"Найдено {len(users_without_auto_renewal)} пользователей без автопродления")
        
        # Проверяем, есть ли пользователи с is_subscribed=False, но с renew_id
        for user in users_without_auto_renewal:
            if user.renew_id:
                logger.warning(f"Пользователь {user.id} имеет renew_id={user.renew_id}, но is_subscribed=False")
        
        # Обрабатываем пользователей без автопродления
        for user in users_without_auto_renewal:
            try:
                days_remaining = (user.expired_at - datetime.now().date()).days
                logger.info(f"Проверка подписки для пользователя {user.id} без автопродления: истекает через {days_remaining} дней")
                
                if days_remaining <= 0:
                    logger.info(f"Подписка пользователя {user.id} истекла, отправляю уведомление об окончании подписки")
                    await on_disabled(user)
                    continue
                
                if days_remaining in [1, 2, 3]:
                    logger.info(f"Отправка уведомления о скором истечении подписки пользователю {user.id} (осталось {days_remaining} дн.)")
                    await notify_expiring_subscription(user)
            except Exception as e:
                logger.error(f"Ошибка при обработке пользователя {user.id} без автопродления: {str(e)}")
                continue
        
        logger.info("Проверка подписок пользователей успешно завершена")
    except Exception as e:
        logger.error(f"Ошибка при проверке подписок пользователей: {str(e)}") 
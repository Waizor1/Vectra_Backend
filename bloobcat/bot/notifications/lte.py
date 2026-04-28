from bloobcat.logger import get_logger

logger = get_logger("notifications.lte")

LTE_THRESHOLD_USER_NOTIFICATIONS_ENABLED = False


async def notify_lte_half_limit(
    user, used_gb: float, total_gb: float, is_trial: bool = False
):
    if LTE_THRESHOLD_USER_NOTIFICATIONS_ENABLED:
        logger.warning(
            "LTE threshold user notifications were expected to stay disabled in this rollout. user=%s used=%s total=%s trial=%s",
            user.id,
            used_gb,
            total_gb,
            is_trial,
        )
        return True
    logger.info(
        "LTE-уведомления отключены. Пропуск notify_lte_half_limit: user=%s used=%s total=%s trial=%s",
        user.id,
        used_gb,
        total_gb,
        is_trial,
    )
    return False


async def notify_lte_full_limit(
    user, used_gb: float, total_gb: float, is_trial: bool = False
):
    if LTE_THRESHOLD_USER_NOTIFICATIONS_ENABLED:
        logger.warning(
            "LTE threshold user notifications were expected to stay disabled in this rollout. user=%s used=%s total=%s trial=%s",
            user.id,
            used_gb,
            total_gb,
            is_trial,
        )
        return True
    logger.info(
        "LTE-уведомления отключены. Пропуск notify_lte_full_limit: user=%s used=%s total=%s trial=%s",
        user.id,
        used_gb,
        total_gb,
        is_trial,
    )
    return False

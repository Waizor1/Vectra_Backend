from fastapi import APIRouter

from .payment import router as payment_router
from .user import router as user_router
from .promo import router as promo_router
from .prize_wheel import router as prize_wheel_router
from .discounts import router as discounts_router
from .captain_user_lookup import router as captain_lookup_router
from .admin_integration import router as admin_integration_router

main_router = APIRouter()

main_router.include_router(payment_router)
main_router.include_router(user_router)
main_router.include_router(promo_router)
main_router.include_router(prize_wheel_router)
main_router.include_router(discounts_router)
main_router.include_router(captain_lookup_router)
main_router.include_router(admin_integration_router)

# Флаг для отслеживания, был ли уже включен маршрутизатор бота
_bot_router_included = False

# Перемещаем импорт bot_router внутрь функции, которая будет вызываться позже
def include_bot_router():
    """
    Включает маршрутизатор бота в основной маршрутизатор.
    Эта функция должна быть вызвана только один раз.
    """
    global _bot_router_included
    
    # Если маршрутизатор бота уже включен, ничего не делаем
    if _bot_router_included:
        return
    
    # Импортируем bot_router внутри функции
    from bloobcat.bot import router as bot_router
    
    # Включаем маршрутизатор бота в основной маршрутизатор
    main_router.include_router(bot_router)
    
    # Устанавливаем флаг, что маршрутизатор бота включен
    _bot_router_included = True

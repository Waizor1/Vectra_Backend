from fastapi import APIRouter

from .marzban.connect import router as marzban_connect_router
from .payment import router as payment_router
from .tv_connect import router as tv_router
from .user import router as user_router

main_router = APIRouter()


main_router.include_router(marzban_connect_router)
main_router.include_router(payment_router)
main_router.include_router(user_router)

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

main_router.include_router(tv_router)

from fastapi import APIRouter
from cyberdog.bot.bot import get_bot_username # Предполагаемый путь

router = APIRouter(prefix="/app", tags=["app_info"])

@router.get("/bot_username")
async def get_bot_username_endpoint():
    """
    Возвращает текущее имя пользователя бота.
    """
    username = await get_bot_username() # Предполагаем, что функция асинхронная
    return {"bot_username": username} 
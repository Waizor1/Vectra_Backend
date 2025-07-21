from aiogram.filters import BaseFilter, CommandObject
from aiogram.types import Message

from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("bot_admin_functions")


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message):
        user = await Users.get_user(message.from_user)
        if not user:
            return False
        return user.is_admin


class IsPartnerOrAdmin(BaseFilter):
    async def __call__(self, message: Message):
        user = await Users.get_user(message.from_user)
        if not user:
            return False
        return user.is_admin or user.is_partner


async def search_user(user_id: str):
    """Поиск пользователя по ID или username (точное совпадение)"""
    if user_id.isdigit():
        return await Users.get_or_none(id=int(user_id))
    user_id = user_id.replace("@", "")
    return await Users.get_or_none(username=user_id)


async def search_users(query: str):
    """
    Поиск пользователей по частичному совпадению
    Возвращает список пользователей
    """
    from tortoise.expressions import Q
    from tortoise import connections
    
    # Убираем @ в начале, если есть
    if query.startswith("@"):
        query = query[1:]
    
    # Строим запрос
    users = []
    
    # Если это число - ищем по ID
    if query.isdigit():
        # Точное совпадение
        user_by_id = await Users.get_or_none(id=int(query))
        if user_by_id:
            users.append(user_by_id)
        
        # Частичное совпадение по ID с использованием raw SQL
        try:
            conn = connections.get("default")
            # Используем правильный синтаксис PostgreSQL для LIKE
            raw_query = """
                SELECT * FROM users 
                WHERE CAST(id AS TEXT) LIKE $1
                LIMIT 20
            """
            raw_users = await conn.execute_query_dict(raw_query, [f"%{query}%"])
            
            # Конвертируем результаты в объекты Users
            for user_data in raw_users:
                user = await Users.get_or_none(id=user_data['id'])
                if user and user not in users:
                    users.append(user)
        except Exception as e:
            logger.warning(f"Ошибка при поиске по частичному ID: {e}")
    
    # Поиск по username и full_name
    users_by_text = await Users.filter(
        Q(username__icontains=query) | Q(full_name__icontains=query)
    ).limit(20)
    
    # Объединяем результаты, убирая дубликаты
    seen_ids = set()
    result = []
    
    for user in users + users_by_text:
        if user.id not in seen_ids:
            seen_ids.add(user.id)
            result.append(user)
    
    return result


async def handle_balance_change(
    message: Message, command: CommandObject, operation: str
):
    if not command.args:
        await message.answer("Введите аргументы")
        return
    user_id, amount = command.args.split()
    amount = (
        int(amount)
        if amount.isdigit()
        else int(amount[1:])
        if amount[0] in "+-"
        else None
    )

    if amount is None:
        await message.answer(f"Неверный формат {operation}")
        return

    user = await search_user(user_id)
    if not user:
        await message.answer("Пользователь не найден")
        return

    if operation == "balance":
        user.balance += amount
    elif operation == "days":
        await user.extend_subscription(amount)

    await user.save()
    await message.answer(
        f"{operation.title()} пользователя {user_id} изменен на {amount}"
    )

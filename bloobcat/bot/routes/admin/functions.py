from aiogram.filters import BaseFilter, CommandObject
from aiogram.types import Message

from bloobcat.db.users import Users


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message):
        user = await Users.get_user(message.from_user)
        return user.is_admin


async def search_user(user_id: str):
    if user_id.isdigit():
        return await Users.get_or_none(id=int(user_id))
    user_id = user_id.replace("@", "")
    return await Users.get_or_none(username=user_id)


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

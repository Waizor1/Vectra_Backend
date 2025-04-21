from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .functions import IsAdmin, search_user

router = Router()


@router.message(Command("unreg"), IsAdmin())
async def unreg(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Введите аргументы")
        return

    user = await search_user(command.args)
    if not user:
        await message.answer("Пользователь не найден")
        return

    user.is_registered = False
    await user.save()
    await message.answer(
        f"Пользователь {command.args} теперь увидит приветствие при следующем входе"
    )

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .functions import IsAdmin, handle_balance_change, search_user

router = Router()


@router.message(Command("balance"), IsAdmin())
async def balance(message: Message, command: CommandObject):
    await handle_balance_change(message, command, "balance")


@router.message(Command("days"), IsAdmin())
async def days(message: Message, command: CommandObject):
    await handle_balance_change(message, command, "days")


@router.message(Command("percent"), IsAdmin())
async def change_percent(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Введите аргументы")
        return

    user_id, percent = command.args.split()
    if not percent.isdigit():
        await message.answer("Неверный процент")
        return

    user = await search_user(user_id)
    if not user:
        await message.answer("Пользователь не найден")
        return

    user.referral_percent = int(percent)
    await user.save()
    await message.answer(
        f"Процент реферальной программы пользователя {user_id} изменен на {percent}"
    )

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from cyberdog.bot.routes.admin.functions import IsAdmin
from cyberdog.db.users import Users
from cyberdog import schedules
from datetime import datetime

router = Router()


@router.message(Command("test_check_subscriptions"), IsAdmin())
async def test_check_subscriptions(message: Message):
    """
    Тестовая команда для проверки функции check_subscriptions
    Выводит список пользователей, которые попадают под условия проверки
    """
    try:
        # Отправляем сообщение о начале проверки
        await message.answer("Начинаю проверку подписок...")
        
        # Получаем список пользователей, которые попадают под условия
        users = await Users.filter(
            is_subscribed=True,
            renew_id__not_isnull=True,
            expired_at__not_isnull=True
        )
        
        # Если пользователей нет, отправляем сообщение
        if not users:
            await message.answer("Не найдено пользователей, подходящих под условия проверки")
            return
        
        # Формируем сообщение со списком пользователей
        user_list = "Список пользователей для проверки подписок:\n\n"
        for user in users:
            days_remaining = (user.expired_at - datetime.now().date()).days
            user_list += f"ID: {user.id}, Имя: {user.name()}, Дней до истечения: {days_remaining}\n"
        
        # Отправляем сообщение со списком пользователей
        await message.answer(user_list)
        
        # Запускаем проверку подписок
        await message.answer("Запускаю проверку подписок...")
        await schedules.check_subscriptions()
        
        # Отправляем сообщение об успешном завершении
        await message.answer("Проверка подписок успешно завершена")
        
    except Exception as e:
        # В случае ошибки отправляем сообщение с текстом ошибки
        error_message = f"Произошла ошибка при проверке подписок: {str(e)}"
        await message.answer(error_message) 
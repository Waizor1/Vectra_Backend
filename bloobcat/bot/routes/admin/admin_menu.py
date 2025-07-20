from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.logger import get_logger

logger = get_logger("bot_admin_menu")
router = Router()

# Reply клавиатура для админов
admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔧 Админ панель")],
        [KeyboardButton(text="🧪 Тесты")]
    ],
    resize_keyboard=True,
    persistent=True
)


@router.message(Command("admin"), IsAdmin())
async def admin_command(message: Message):
    """Устанавливает админскую клавиатуру и показывает главное меню"""
    from .keyboards import get_main_admin_menu
    
    # Устанавливаем reply клавиатуру
    await message.answer(
        "🔧 **Админская клавиатура установлена!**\n"
        "Используйте кнопки ниже для быстрого доступа к функциям.",
        reply_markup=admin_keyboard,
        parse_mode="Markdown"
    )
    
    # Показываем главное меню
    await message.answer(
        "🔧 **АДМИН ПАНЕЛЬ**\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_main_admin_menu(),
        parse_mode="Markdown"
    )
    
    logger.info(f"Админ {message.from_user.id} открыл админ панель")


@router.message(F.text == "🔧 Админ панель", IsAdmin())
async def admin_panel_button(message: Message):
    """Обработчик кнопки 'Админ панель' из reply клавиатуры"""
    from .keyboards import get_main_admin_menu
    
    await message.answer(
        "🔧 **АДМИН ПАНЕЛЬ**\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_main_admin_menu(),
        parse_mode="Markdown"
    )


@router.message(F.text == "🧪 Тесты", IsAdmin())
async def test_panel_button(message: Message):
    """Обработчик кнопки 'Тесты' из reply клавиатуры"""
    from .keyboards import get_test_menu
    
    await message.answer(
        "🧪 **ТЕСТОВАЯ ПАНЕЛЬ**\n\n"
        "Выберите тип тестирования:",
        reply_markup=get_test_menu(),
        parse_mode="Markdown"
    )


@router.message(Command("remove_admin_keyboard"), IsAdmin())
async def remove_admin_keyboard_command(message: Message):
    """Команда для удаления админской клавиатуры"""
    try:
        await message.answer(
            "✅ Админская клавиатура удалена",
            reply_markup=ReplyKeyboardRemove()
        )
        logger.info(f"Админ {message.from_user.id} удалил админскую клавиатуру")
    except Exception as e:
        error_message = f"Ошибка при удалении клавиатуры: {str(e)}"
        await message.answer(error_message)
        logger.error(error_message)


@router.message(Command("setup_admin_keyboard"), IsAdmin())
async def setup_admin_keyboard_command(message: Message):
    """Принудительная установка админской клавиатуры (для отладки)"""
    success = await setup_admin_keyboard_for_user(message.bot, message.from_user.id)
    if success:
        await message.answer("✅ Админская клавиатура успешно установлена!")
    else:
        await message.answer("❌ Ошибка при установке админской клавиатуры")


@router.message(Command("setup_keyboard_for_user"), IsAdmin())
async def setup_keyboard_for_user_command(message: Message):
    """Установка админской клавиатуры для указанного пользователя"""
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer(
            "Ошибка: Укажите ID пользователя.\n"
            "Пример: /setup_keyboard_for_user 123456789"
        )
        return
    
    user_id = int(args[1])
    success = await setup_admin_keyboard_for_user(message.bot, user_id)
    if success:
        await message.answer(f"✅ Админская клавиатура установлена для пользователя {user_id}")
    else:
        await message.answer(f"❌ Пользователь {user_id} не найден или не является админом")


@router.message(Command("setup_all_admin_keyboards"), IsAdmin())
async def setup_all_admin_keyboards_command(message: Message):
    """Установка админских клавиатур для всех админов"""
    await message.answer("⏳ Устанавливаю клавиатуры для всех админов...")
    
    success_count, total_count = await setup_keyboards_for_all_admins(message.bot)
    
    await message.answer(
        f"✅ **Установка завершена!**\n\n"
        f"📊 Результат: {success_count}/{total_count} админов\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {total_count - success_count}",
        parse_mode="Markdown"
    )


# Функция для установки клавиатуры админу программно
async def set_admin_keyboard(bot, user_id: int):
    """Устанавливает админскую клавиатуру для пользователя"""
    try:
        await bot.send_message(
            user_id,
            "🔧 **Вам предоставлены права администратора!**\n"
            "Используйте кнопки ниже для доступа к админским функциям.",
            reply_markup=admin_keyboard,
            parse_mode="Markdown"
        )
        logger.info(f"Установлена админская клавиатура для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка установки админской клавиатуры для {user_id}: {e}")


async def remove_admin_keyboard(bot, user_id: int):
    """Убирает админскую клавиатуру у пользователя"""
    try:
        await bot.send_message(
            user_id,
            "❌ **Права администратора отозваны**",
            reply_markup=ReplyKeyboardRemove()
        )
        logger.info(f"Удалена админская клавиатура для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка удаления админской клавиатуры для {user_id}: {e}")


async def setup_admin_keyboard_for_user(bot, user_id: int):
    """
    Универсальная функция для установки админской клавиатуры пользователю
    Проверяет в БД является ли пользователь админом и устанавливает клавиатуру
    """
    try:
        from bloobcat.db.users import Users
        
        user = await Users.get_or_none(id=user_id)
        if user and user.is_admin:
            await set_admin_keyboard(bot, user_id)
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка при установке админской клавиатуры для {user_id}: {e}")
        return False


async def remove_admin_keyboard_for_user(bot, user_id: int):
    """
    Универсальная функция для удаления админской клавиатуры
    Используется при снятии админских прав
    """
    try:
        await remove_admin_keyboard(bot, user_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка при удалении админской клавиатуры для {user_id}: {e}")
        return False


async def setup_keyboards_for_all_admins(bot):
    """
    Устанавливает админские клавиатуры для всех админов в БД
    Полезно при запуске бота
    """
    try:
        from bloobcat.db.users import Users
        
        # Получаем всех админов
        admins = await Users.filter(is_admin=True)
        
        success_count = 0
        total_count = len(admins)
        
        for admin in admins:
            try:
                await set_admin_keyboard(bot, admin.id)
                success_count += 1
                logger.debug(f"Установлена клавиатура для админа {admin.id}")
            except Exception as e:
                logger.warning(f"Не удалось установить клавиатуру для админа {admin.id}: {e}")
        
        logger.info(f"Установлены клавиатуры для {success_count}/{total_count} админов")
        return success_count, total_count
        
    except Exception as e:
        logger.error(f"Ошибка при установке клавиатур для всех админов: {e}")
        return 0, 0 

 
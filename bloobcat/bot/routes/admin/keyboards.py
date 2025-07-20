from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_main_admin_menu() -> InlineKeyboardMarkup:
    """Главное админ меню"""
    builder = InlineKeyboardBuilder()
    
    # Первый ряд
    builder.row(
        InlineKeyboardButton(text="👥 Управление пользователями", callback_data="admin:users"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")
    )
    
    # Второй ряд
    builder.row(
        InlineKeyboardButton(text="⚙️ Системные операции", callback_data="admin:system"),
        InlineKeyboardButton(text="📢 Рассылки", callback_data="admin:broadcasts")
    )
    
    # Третий ряд
    builder.row(
        InlineKeyboardButton(text="🛠️ Утилиты", callback_data="admin:utils")
    )
    
    return builder.as_markup()


def get_users_menu() -> InlineKeyboardMarkup:
    """Меню управления пользователями"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin:users:stats"),
        InlineKeyboardButton(text="🔍 Управление пользователем", callback_data="admin:users:manage")
    )
    
    builder.row(
        InlineKeyboardButton(text="🚫 Заблокировавшие бота", callback_data="admin:users:blocked")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_blocked_users_menu() -> InlineKeyboardMarkup:
    """Меню для пользователей что заблокировали бота"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📋 Список заблокировавших", callback_data="admin:blocked:list"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin:blocked:stats")
    )
    
    builder.row(
        InlineKeyboardButton(text="🗑️ Очистка из БД", callback_data="admin:blocked:cleanup")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Управление пользователями", callback_data="admin:users"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_stats_menu() -> InlineKeyboardMarkup:
    """Меню статистики"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🎯 UTM статистика", callback_data="admin:stats:utm"),
        InlineKeyboardButton(text="📅 Временная статистика", callback_data="admin:stats:time")
    )
    
    builder.row(
        InlineKeyboardButton(text="🌐 Онлайн пользователи", callback_data="admin:stats:online"),
        InlineKeyboardButton(text="🆓 Trial период", callback_data="admin:stats:trial")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_utm_stats_menu() -> InlineKeyboardMarkup:
    """Меню UTM статистики"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📈 Общая статистика UTM", callback_data="admin:utm:all"),
        InlineKeyboardButton(text="🔗 По конкретному UTM", callback_data="admin:utm:specific")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Статистика", callback_data="admin:stats"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_time_stats_menu() -> InlineKeyboardMarkup:
    """Меню временной статистики"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📅 За сегодня", callback_data="admin:time:today"),
        InlineKeyboardButton(text="📅 За вчера", callback_data="admin:time:yesterday")
    )
    
    builder.row(
        InlineKeyboardButton(text="📅 За неделю", callback_data="admin:time:week"),
        InlineKeyboardButton(text="📅 За месяц", callback_data="admin:time:month")
    )
    
    builder.row(
        InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin:time:total")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Статистика", callback_data="admin:stats"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_system_menu() -> InlineKeyboardMarkup:
    """Меню системных операций"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="⏰ Истекающие подписки", callback_data="admin:system:expiring")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_utils_menu() -> InlineKeyboardMarkup:
    """Меню утилит"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔗 UTM генератор", callback_data="admin:utils:utm"),
        InlineKeyboardButton(text="🔧 Обновить меню бота", callback_data="admin:utils:setmenu")
    )
    
    builder.row(
        InlineKeyboardButton(text="❓ Справка по командам", callback_data="admin:utils:help")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin:main"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_test_menu() -> InlineKeyboardMarkup:
    """Главное тестовое меню"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📢 Тестирование уведомлений", callback_data="test:notifications"),
        InlineKeyboardButton(text="💳 Тестирование платежей", callback_data="test:payments")
    )
    
    builder.row(
        InlineKeyboardButton(text="📊 Тестирование статистики", callback_data="test:stats"),
        InlineKeyboardButton(text="🔧 Другие тесты", callback_data="test:other")
    )
    
    return builder.as_markup()


def get_test_other_menu() -> InlineKeyboardMarkup:
    """Меню других тестов (системные операции)"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔄 Проверка подписок", callback_data="test:other:check_subs"),
        InlineKeyboardButton(text="🆓 Проверка trial", callback_data="test:other:check_trial")
    )
    
    builder.row(
        InlineKeyboardButton(text="🔄 Все проверки", callback_data="test:other:check_all")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Тестовое меню", callback_data="test:main"),
        InlineKeyboardButton(text="🏠 Админ панель", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_test_notifications_menu() -> InlineKeyboardMarkup:
    """Меню тестирования уведомлений"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🆓 Trial уведомления", callback_data="test:notif:trial"),
        InlineKeyboardButton(text="💰 Subscription уведомления", callback_data="test:notif:subscription")
    )
    
    builder.row(
        InlineKeyboardButton(text="📱 Общие уведомления", callback_data="test:notif:general")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Тестовое меню", callback_data="test:main"),
        InlineKeyboardButton(text="🏠 Админ панель", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_test_payments_menu() -> InlineKeyboardMarkup:
    """Меню тестирования платежей"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🚀 Запустить автоплатеж", callback_data="test:pay:trigger"),
        InlineKeyboardButton(text="📧 Уведомление о списании", callback_data="test:pay:notice")
    )
    
    builder.row(
        InlineKeyboardButton(text="📋 Готовые к автоплатежу", callback_data="test:pay:ready"),
        InlineKeyboardButton(text="📜 История автоплатежей", callback_data="test:pay:history")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Тестовое меню", callback_data="test:main"),
        InlineKeyboardButton(text="🏠 Админ панель", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_test_stats_menu() -> InlineKeyboardMarkup:
    """Меню тестирования статистики"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📅 Тест дневной статистики", callback_data="test:stats:daily"),
        InlineKeyboardButton(text="📅 Тест недельной статистики", callback_data="test:stats:weekly")
    )
    
    builder.row(
        InlineKeyboardButton(text="📅 Тест месячной статистики", callback_data="test:stats:monthly")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Тестовое меню", callback_data="test:main"),
        InlineKeyboardButton(text="🏠 Админ панель", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_user_management_menu(user_id: int, user_name: str) -> InlineKeyboardMarkup:
    """Меню управления конкретным пользователем"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="📊 Информация", callback_data=f"admin:user:{user_id}:info"),
        InlineKeyboardButton(text="💰 Подписка", callback_data=f"admin:user:{user_id}:subscription")
    )
    
    builder.row(
        InlineKeyboardButton(text="🔄 Автопродление", callback_data=f"admin:user:{user_id}:renewal"),
        InlineKeyboardButton(text="🆓 Trial", callback_data=f"admin:user:{user_id}:trial")
    )
    
    builder.row(
        InlineKeyboardButton(text="🚫 Статус блокировки", callback_data=f"admin:user:{user_id}:block"),
        InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin:user:{user_id}:delete")
    )
    
    # Навигация
    builder.row(
        InlineKeyboardButton(text="◀️ Управление пользователями", callback_data="admin:users"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_back_to_main_menu() -> InlineKeyboardMarkup:
    """Простая кнопка возврата в главное меню"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    return builder.as_markup()


def get_broadcast_audience_menu() -> InlineKeyboardMarkup:
    """Клавиатура выбора аудитории для рассылки"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="Всем пользователям", callback_data="broadcast:audience:all")
    )
    builder.row(
        InlineKeyboardButton(text="Только активным", callback_data="broadcast:audience:active")
    )
    builder.row(
        InlineKeyboardButton(text="Неактивным пользователям", callback_data="broadcast:audience:inactive")
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:main")
    )
    
    return builder.as_markup()


def get_confirmation_keyboard(action: str, params: str = "") -> InlineKeyboardMarkup:
    """Клавиатура подтверждения действия"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{action}:{params}"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="admin:main")
    )
    
    return builder.as_markup() 
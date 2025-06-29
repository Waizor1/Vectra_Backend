from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import traceback

from bloobcat.bot.routes.admin.functions import IsAdmin, search_user
from bloobcat.logger import get_logger

# Импорты всех функций уведомлений
from bloobcat.bot.notifications.trial.extended import notify_trial_extended
from bloobcat.bot.notifications.trial.granted import notify_trial_granted
from bloobcat.bot.notifications.trial.expiring import notify_expiring_trial
from bloobcat.bot.notifications.trial.end import notify_trial_ended
from bloobcat.bot.notifications.trial.no_trial import notify_no_trial_taken

from bloobcat.bot.notifications.subscription.expiration import notify_expiring_subscription, notify_auto_payment
from bloobcat.bot.notifications.subscription.renewal import notify_auto_renewal_success_balance, notify_auto_renewal_failure
from bloobcat.bot.notifications.subscription.key import on_disabled

from bloobcat.bot.notifications.general.activation import on_activated_key
from bloobcat.bot.notifications.general.referral import on_referral_payment, on_referral_registration, on_referral_prompt

logger = get_logger("bot_admin_test_notifications")

router = Router()


class TestNotificationFSM(StatesGroup):
    waiting_for_notification_type = State()
    waiting_for_specific_notification = State()
    waiting_for_user_id = State()
    waiting_for_additional_params = State()
    waiting_for_confirmation = State()


# Структура уведомлений для интерфейса
NOTIFICATION_TYPES = {
    "trial": {
        "name": "🔄 Пробный период",
        "notifications": {
            "extended": {"name": "Продление пробного периода", "params": ["days"]},
            "granted": {"name": "Предоставление пробного периода", "params": []},
            "expiring": {"name": "Скоро истекает пробный период", "params": []},
            "end": {"name": "Окончание пробного периода", "params": []},
            "no_trial": {"name": "Нет пробного периода", "params": ["hours_passed"]}
        }
    },
    "subscription": {
        "name": "💰 Подписка",
        "notifications": {
            "expiring": {"name": "Истекает подписка", "params": []},
            "auto_payment": {"name": "Автоматический платеж", "params": []},
            "renewal_success": {"name": "Успешное продление с баланса", "params": ["days", "amount"]},
            "renewal_failure": {"name": "Ошибка автопродления", "params": []},
            "key_disabled": {"name": "Ключ отключен", "params": []}
        }
    },
    "general": {
        "name": "📱 Общие",
        "notifications": {
            "activation": {"name": "Активация ключа", "params": []},
            "referral_payment": {"name": "Реферальный платеж", "params": ["referral_id", "amount"]},
            "referral_registration": {"name": "Регистрация по рефералу", "params": ["referral_id"]},
            "referral_prompt": {"name": "Напоминание о реферале", "params": ["days"]}
        }
    }
}


@router.message(Command("test_notification"), IsAdmin())
async def start_test_notification(message: Message, state: FSMContext):
    """Начало процесса тестирования уведомлений"""
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=NOTIFICATION_TYPES[key]["name"], 
            callback_data=f"test_notif_type:{key}"
        )] for key in NOTIFICATION_TYPES.keys()
    ] + [[InlineKeyboardButton(text="❌ Отменить", callback_data="test_notif_cancel")]])
    
    await message.answer(
        "🔧 **Тестирование уведомлений**\n\n"
        "Выберите тип уведомления для тестирования:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    await state.set_state(TestNotificationFSM.waiting_for_notification_type)


@router.callback_query(TestNotificationFSM.waiting_for_notification_type, lambda c: c.data.startswith("test_notif_type:") or c.data == "test_notif_cancel", IsAdmin())
async def process_notification_type(callback_query: CallbackQuery, state: FSMContext):
    if callback_query.data == "test_notif_cancel":
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("❌ Тестирование уведомлений отменено")
        await state.clear()
        return
    
    notification_type = callback_query.data.split(":", 1)[1]
    await state.update_data(notification_type=notification_type)
    
    notifications = NOTIFICATION_TYPES[notification_type]["notifications"]
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=info["name"], 
            callback_data=f"test_notif_specific:{key}"
        )] for key, info in notifications.items()
    ] + [[InlineKeyboardButton(text="🔙 Назад", callback_data="test_notif_back")],
         [InlineKeyboardButton(text="❌ Отменить", callback_data="test_notif_cancel")]])
    
    await callback_query.message.edit_text(
        f"🔧 **Тестирование уведомлений**\n\n"
        f"Тип: {NOTIFICATION_TYPES[notification_type]['name']}\n\n"
        f"Выберите конкретное уведомление:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    await state.set_state(TestNotificationFSM.waiting_for_specific_notification)


@router.callback_query(TestNotificationFSM.waiting_for_specific_notification, lambda c: c.data.startswith("test_notif_specific:") or c.data in ["test_notif_back", "test_notif_cancel"], IsAdmin())
async def process_specific_notification(callback_query: CallbackQuery, state: FSMContext):
    if callback_query.data == "test_notif_cancel":
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("❌ Тестирование уведомлений отменено")
        await state.clear()
        return
    
    if callback_query.data == "test_notif_back":
        await start_test_notification(callback_query.message, state)
        return
    
    specific_notification = callback_query.data.split(":", 1)[1]
    data = await state.get_data()
    notification_type = data["notification_type"]
    
    await state.update_data(specific_notification=specific_notification)
    
    notification_info = NOTIFICATION_TYPES[notification_type]["notifications"][specific_notification]
    
    await callback_query.message.edit_text(
        f"🔧 **Тестирование уведомлений**\n\n"
        f"Тип: {NOTIFICATION_TYPES[notification_type]['name']}\n"
        f"Уведомление: {notification_info['name']}\n\n"
        f"Введите ID пользователя или @username для отправки уведомления:\n"
        f"(Пример: 123456789 или @username)",
        reply_markup=None,
        parse_mode="Markdown"
    )
    await state.set_state(TestNotificationFSM.waiting_for_user_id)


@router.message(TestNotificationFSM.waiting_for_user_id)
async def process_user_id(message: Message, state: FSMContext):
    user_input = message.text.strip()
    
    if user_input == "/cancel":
        await message.answer("❌ Тестирование уведомлений отменено")
        await state.clear()
        return
    
    # Поиск пользователя
    user = await search_user(user_input)
    if not user:
        await message.answer(
            "❌ Пользователь не найден!\n"
            "Попробуйте еще раз или введите /cancel для отмены"
        )
        return
    
    await state.update_data(target_user=user)
    
    data = await state.get_data()
    notification_type = data["notification_type"]
    specific_notification = data["specific_notification"]
    notification_info = NOTIFICATION_TYPES[notification_type]["notifications"][specific_notification]
    
    # Проверяем, нужны ли дополнительные параметры
    if notification_info["params"]:
        params_text = ", ".join(notification_info["params"])
        await message.answer(
            f"👤 Пользователь найден: {user.full_name} (ID: {user.id})\n\n"
            f"Для этого уведомления требуются дополнительные параметры: {params_text}\n"
            f"Введите значения через пробел:\n"
            f"(Например: 7 для days или 100 для bonus_amount)"
        )
        await state.set_state(TestNotificationFSM.waiting_for_additional_params)
    else:
        # Сразу переходим к подтверждению
        await show_confirmation(message, state, user, notification_type, specific_notification)


@router.message(TestNotificationFSM.waiting_for_additional_params)
async def process_additional_params(message: Message, state: FSMContext):
    if message.text.strip() == "/cancel":
        await message.answer("❌ Тестирование уведомлений отменено")
        await state.clear()
        return
    
    data = await state.get_data()
    notification_type = data["notification_type"]
    specific_notification = data["specific_notification"]
    
    try:
        # Специальная обработка для реферальных уведомлений
        if notification_type == "general" and specific_notification in ["referral_payment", "referral_registration"]:
            input_parts = message.text.strip().split()
            if len(input_parts) < 1:
                raise ValueError("Недостаточно параметров")
            
            # Первый параметр - ID реферала
            referral_id = input_parts[0]
            referral_user = await search_user(referral_id)
            if not referral_user:
                await message.answer(
                    "❌ Реферальный пользователь не найден!\n"
                    "Попробуйте еще раз или введите /cancel для отмены"
                )
                return
            
            if specific_notification == "referral_payment":
                if len(input_parts) < 2:
                    raise ValueError("Для реферального платежа нужны: referral_id amount")
                amount = int(input_parts[1])
                params = [referral_user, amount]
            else:  # referral_registration
                params = [referral_user]
        else:
            # Обычная обработка для числовых параметров
            params = [int(x) for x in message.text.strip().split()]
        
        await state.update_data(additional_params=params)
        
        target_user = data["target_user"]
        await show_confirmation(message, state, target_user, notification_type, specific_notification, params)
        
    except ValueError as e:
        param_examples = {
            ("general", "referral_payment"): "user_id amount (например: 123456789 100)",
            ("general", "referral_registration"): "user_id (например: 123456789)",
            ("general", "referral_prompt"): "days (например: 7)",
            ("trial", "extended"): "days (например: 7)",
            ("trial", "no_trial"): "hours_passed (например: 24)",
            ("subscription", "renewal_success"): "days amount (например: 30 500)"
        }
        example = param_examples.get((notification_type, specific_notification), "числовые значения через пробел")
        
        await message.answer(
            f"❌ Неверный формат параметров!\n"
            f"Ожидается: {example}\n"
            f"Попробуйте еще раз или введите /cancel для отмены"
        )


async def show_confirmation(message: Message, state: FSMContext, user, notification_type: str, specific_notification: str, params=None):
    """Показывает подтверждение перед отправкой"""
    notification_info = NOTIFICATION_TYPES[notification_type]["notifications"][specific_notification]
    
    # Форматируем параметры для отображения
    params_text = ""
    if params:
        if notification_type == "general" and specific_notification in ["referral_payment", "referral_registration"]:
            if specific_notification == "referral_payment":
                referral_user, amount = params
                params_text = f"\n📊 **Реферал:** {referral_user.full_name} (ID: {referral_user.id})\n💰 **Сумма:** {amount}₽"
            else:  # referral_registration
                referral_user = params[0]
                params_text = f"\n📊 **Реферал:** {referral_user.full_name} (ID: {referral_user.id})"
        else:
            # Обычные числовые параметры
            param_names = notification_info["params"]
            param_pairs = [f"{name}: {value}" for name, value in zip(param_names, params)]
            params_text = f"\n📋 **Параметры:** {', '.join(param_pairs)}"
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="test_notif_confirm")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="test_notif_cancel")]
    ])
    
    await message.answer(
        f"🔧 **Подтверждение отправки**\n\n"
        f"👤 **Получатель:** {user.full_name} (ID: {user.id})\n"
        f"📩 **Уведомление:** {notification_info['name']}\n"
        f"📂 **Тип:** {NOTIFICATION_TYPES[notification_type]['name']}{params_text}\n\n"
        f"Отправить уведомление?",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    await state.set_state(TestNotificationFSM.waiting_for_confirmation)


@router.callback_query(TestNotificationFSM.waiting_for_confirmation, lambda c: c.data in ["test_notif_confirm", "test_notif_cancel"], IsAdmin())
async def process_confirmation(callback_query: CallbackQuery, state: FSMContext):
    if callback_query.data == "test_notif_cancel":
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("❌ Тестирование уведомлений отменено")
        await state.clear()
        return
    
    await callback_query.answer()
    await callback_query.message.edit_reply_markup(reply_markup=None)
    
    data = await state.get_data()
    target_user = data["target_user"]
    notification_type = data["notification_type"]
    specific_notification = data["specific_notification"]
    additional_params = data.get("additional_params", [])
    
    # Получаем функцию уведомления
    notification_function = get_notification_function(notification_type, specific_notification)
    if not notification_function:
        await callback_query.message.answer("❌ Ошибка: функция уведомления не найдена!")
        await state.clear()
        return
    
    progress_message = await callback_query.message.answer("📤 Отправляю уведомление...")
    
    try:
        # Вызываем функцию уведомления с параметрами
        if additional_params:
            await notification_function(target_user, *additional_params)
        else:
            await notification_function(target_user)
        
        await progress_message.edit_text(
            f"✅ **Уведомление успешно отправлено!**\n\n"
            f"👤 Пользователь: {target_user.full_name} (ID: {target_user.id})\n"
            f"📩 Уведомление: {NOTIFICATION_TYPES[notification_type]['notifications'][specific_notification]['name']}",
            parse_mode="Markdown"
        )
        logger.info(f"Тестовое уведомление {notification_type}.{specific_notification} успешно отправлено пользователю {target_user.id}")
        
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Ошибка при отправке тестового уведомления: {e}\n{tb}")
        await progress_message.edit_text(
            f"❌ **Ошибка при отправке уведомления:**\n\n"
            f"`{str(e)}`\n\n"
            f"Проверьте логи для подробностей.",
            parse_mode="Markdown"
        )
    
    await state.clear()


def get_notification_function(notification_type: str, specific_notification: str):
    """Возвращает функцию уведомления по типу и названию"""
    function_map = {
        "trial": {
            "extended": notify_trial_extended,
            "granted": notify_trial_granted,
            "expiring": notify_expiring_trial,
            "end": notify_trial_ended,
            "no_trial": notify_no_trial_taken
        },
        "subscription": {
            "expiring": notify_expiring_subscription,
            "auto_payment": notify_auto_payment,
            "renewal_success": notify_auto_renewal_success_balance,
            "renewal_failure": notify_auto_renewal_failure,
            "key_disabled": on_disabled
        },
        "general": {
            "activation": on_activated_key,
            "referral_payment": on_referral_payment,
            "referral_registration": on_referral_registration,
            "referral_prompt": on_referral_prompt
        }
    }
    
    return function_map.get(notification_type, {}).get(specific_notification)


@router.message(Command("cancel"), IsAdmin())
async def cancel_test_notification(message: Message, state: FSMContext):
    """Отмена текущего тестирования уведомлений"""
    current_state = await state.get_state()
    if current_state and current_state.startswith("TestNotificationFSM"):
        await message.answer("❌ Тестирование уведомлений отменено")
        await state.clear()
    else:
        await message.answer("🤷‍♂️ Нет активного процесса тестирования уведомлений") 
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import traceback
from datetime import date

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from aiogram import Bot
from .keyboards import get_back_to_main_menu
from .states import SendFSM

logger = get_logger("bot_admin_send")

router = Router()


@router.message(Command("send"), IsAdmin())
async def send(message: Message, state: FSMContext):
    """Начало процесса рассылки сообщений"""
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Всем пользователям", callback_data="send_audience:all")],
        [InlineKeyboardButton(text="Только активным", callback_data="send_audience:active")],
        [InlineKeyboardButton(text="Неактивным пользователям", callback_data="send_audience:inactive")],
        [InlineKeyboardButton(text="Отменить", callback_data="send_cancel")]
    ])
    await message.answer(
        "Выберите аудиторию для рассылки:",
        reply_markup=markup
    )
    await state.set_state(SendFSM.waiting_for_audience)





@router.callback_query(SendFSM.waiting_for_audience, lambda c: c.data.startswith("send_audience:") or c.data == "send_cancel", IsAdmin())
async def process_audience_callback(callback_query: CallbackQuery, state: FSMContext):
    if callback_query.data == "send_cancel":
        await callback_query.message.edit_reply_markup()
        await callback_query.message.answer("Отменено")
        await state.clear()
        return
    key = callback_query.data.split(":", 1)[1]
    audience_map = {"all": "Всем пользователям", "active": "Только активным", "inactive": "Неактивным пользователям"}
    audience_type = audience_map.get(key, "Всем пользователям")
    await state.update_data(audience=audience_type)
    await callback_query.answer()
    await callback_query.message.edit_reply_markup()
    await callback_query.message.answer(
        "Введите текст для рассылки. Поддерживается текст и одно вложение (фото, видео, документ)."
    )
    await state.set_state(SendFSM.waiting_for_message)


@router.message(SendFSM.waiting_for_message)
async def send_message_(message: Message, state: FSMContext):
    """Отправка сообщения выбранной аудитории"""
    if not message.text and not message.photo and not message.video and not message.document:
        await message.answer("Сообщение должно содержать текст или вложение. Попробуйте еще раз.")
        return
    await state.update_data(orig_chat_id=message.chat.id, orig_message_id=message.message_id)
    
    # Создаем клавиатуру с кнопкой возврата в главное меню
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="send_confirm")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="send_cancel")]
    ])
    
    await message.copy_to(message.chat.id, reply_markup=markup)
    await state.set_state(SendFSM.waiting_for_confirmation)


@router.callback_query(SendFSM.waiting_for_confirmation, lambda c: c.data == "send_confirm", IsAdmin())
async def confirm_broadcast(callback_query: CallbackQuery, state: FSMContext, bot: Bot):
    await callback_query.answer()
    data = await state.get_data()
    audience_type = data.get("audience", "Всем пользователям")
    orig_chat_id = data.get("orig_chat_id")
    orig_message_id = data.get("orig_message_id")
    await callback_query.message.edit_reply_markup()
    progress_message = await callback_query.message.answer(f"Начинаю рассылку ({audience_type})...")
    await state.clear()
    try:
        today = date.today()
        if audience_type == "Всем пользователям":
            all_users = await Users.all()
        elif audience_type == "Только активным":
            all_users = await Users.filter(
                is_registered=True,
                expired_at__not_isnull=True,
                expired_at__gt=today,
                connected_at__not_isnull=True
            )
        else:
            never_connected = await Users.filter(connected_at__isnull=True)
            expired = await Users.filter(
                is_registered=True,
                expired_at__not_isnull=True,
                expired_at__lte=today
            )
            all_users = never_connected + expired
            logger.info(
                f"Состав неактивных пользователей для рассылки: никогда не подключались: {len(never_connected)}, с истекшей подпиской: {len(expired)}, всего: {len(all_users)}"
            )
        total = len(all_users)
        success = failure = 0
        update_interval = max(1, min(10, total // 10))
        for i, user in enumerate(all_users):
            try:
                await bot.copy_message(chat_id=user.id, from_chat_id=orig_chat_id, message_id=orig_message_id)
                success += 1
            except Exception as e:
                failure += 1
                if "Forbidden" in str(e):
                    logger.debug("Пользователь, вероятно, заблокировал бота")
                logger.error(f"Ошибка при отправке сообщению {user.id}: {e}")
            if (i+1) % update_interval == 0 or i+1 == total:
                percent = round((i+1)/total*100)
                try:
                    await progress_message.edit_text(
                        f"Рассылка в процессе... {i+1}/{total} ({percent}%)\n✅ Успешно: {success}\n❌ Ошибок: {failure}"
                    )
                except Exception as err:
                    logger.error(f"Не удалось обновить прогресс: {err}")
            await asyncio.sleep(0.05)
        final_text = (
            f"✅ **РАССЫЛКА ЗАВЕРШЕНА!**\n\n📊 **Статистика:**\n👥 Всего пользователей: {total}\n✅ Успешно: {success}\n❌ Ошибок: {failure}"
        )
        
        # Добавляем кнопку возврата в главное меню
        await progress_message.edit_text(
            final_text,
            reply_markup=get_back_to_main_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Критическая ошибка при рассылке: {e}\n{tb}")
        await progress_message.edit_text(
            f"❌ **ОШИБКА РАССЫЛКИ**\n\nПроизошла ошибка: {e}\n\nЕсли ошибка повторяется, обратитесь к разработчику.",
            reply_markup=get_back_to_main_menu(),
            parse_mode="Markdown"
        )


@router.callback_query(SendFSM.waiting_for_confirmation, lambda c: c.data == "send_cancel", IsAdmin())
async def cancel_broadcast(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await callback_query.message.edit_reply_markup()
    await callback_query.message.answer(
        "❌ **РАССЫЛКА ОТМЕНЕНА**",
        reply_markup=get_back_to_main_menu(),
        parse_mode="Markdown"
    )
    await state.clear()
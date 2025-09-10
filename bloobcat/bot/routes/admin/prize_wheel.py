from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bloobcat.bot.notifications.prize_wheel import handle_prize_confirmation
from bloobcat.logger import get_logger


logger = get_logger("admin_prize_wheel")
router = Router()


@router.callback_query(F.data.startswith("prize_confirm:"))
async def confirm_prize(callback: CallbackQuery):
    try:
        history_id = int(callback.data.split(":", 1)[1])
        await handle_prize_confirmation(
            admin_id=callback.from_user.id,
            history_id=history_id,
            confirmed=True,
            bot=callback.bot,
        )
        # Убираем кнопки у исходного сообщения, чтобы нельзя было нажать повторно
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.answer("Приз подтвержден!")
    except Exception as e:
        logger.error(f"Ошибка при подтверждении приза: {e}")
        await callback.answer("Ошибка при подтверждении приза")


@router.callback_query(F.data.startswith("prize_reject:"))
async def reject_prize(callback: CallbackQuery):
    try:
        history_id = int(callback.data.split(":", 1)[1])
        await handle_prize_confirmation(
            admin_id=callback.from_user.id,
            history_id=history_id,
            confirmed=False,
            bot=callback.bot,
        )
        # Убираем кнопки у исходного сообщения
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.answer("Приз отклонен!")
    except Exception as e:
        logger.error(f"Ошибка при отклонении приза: {e}")
        await callback.answer("Ошибка при отклонении приза")


@router.callback_query(F.data.startswith("prize_confirm_prompt:"))
async def confirm_prize_prompt(callback: CallbackQuery):
    try:
        history_id = int(callback.data.split(":", 1)[1])
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, подтвердить",
                        callback_data=f"prize_confirm:{history_id}",
                    ),
                    InlineKeyboardButton(
                        text="↩️ Назад",
                        callback_data=f"prize_back:{history_id}",
                    ),
                ]
            ]
        )
        # Редактируем клавиатуру исходного сообщения (без отправки нового)
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer("Подтвердить?")
    except Exception as e:
        logger.error(f"Ошибка при показе промпта подтверждения: {e}")
        await callback.answer("Ошибка")


@router.callback_query(F.data.startswith("prize_reject_prompt:"))
async def reject_prize_prompt(callback: CallbackQuery):
    try:
        history_id = int(callback.data.split(":", 1)[1])
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Да, отклонить",
                        callback_data=f"prize_reject:{history_id}",
                    ),
                    InlineKeyboardButton(
                        text="↩️ Назад",
                        callback_data=f"prize_back:{history_id}",
                    ),
                ]
            ]
        )
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer("Отклонить?")
    except Exception as e:
        logger.error(f"Ошибка при показе промпта отклонения: {e}")
        await callback.answer("Ошибка")


@router.callback_query(F.data.startswith("prize_back:"))
async def back_to_admin_notify(callback: CallbackQuery):
    """Возврат к исходной клавиатуре уведомления админа (без изменения текста)."""
    try:
        history_id = int(callback.data.split(":", 1)[1])
        # Восстанавливаем оригинальную клавиатуру с confirm/reject и 'Написать'
        from bloobcat.db.prize_wheel import PrizeWheelHistory
        from bloobcat.db.users import Users
        entry = await PrizeWheelHistory.get_or_none(id=history_id)
        if not entry:
            await callback.answer("История не найдена")
            return
        user = await Users.get_or_none(id=entry.user_id)
        user_id = user.id if user else 0

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"prize_confirm_prompt:{history_id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"prize_reject_prompt:{history_id}"),
                ],
                [
                    InlineKeyboardButton(text="📝 Написать", url=f"tg://user?id={user_id}")
                ],
            ]
        )
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer("Отмена")
    except Exception as e:
        logger.error(f"Ошибка возврата к исходной клавиатуре: {e}")
        await callback.answer("Ошибка")


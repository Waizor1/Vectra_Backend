from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
import asyncio
import traceback
from datetime import date

from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("bot_admin_send")

router = Router()


class SendFSM(StatesGroup):
    waiting_for_audience = State()
    waiting_for_message = State()


@router.message(Command("send"), IsAdmin())
async def send(message: Message, state: FSMContext):
    """Начало процесса рассылки сообщений"""
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Всем пользователям")],
            [KeyboardButton(text="Только активным")],
            [KeyboardButton(text="Неактивным пользователям")],
            [KeyboardButton(text="/cancel")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.answer(
        "Выберите аудиторию для рассылки:",
        reply_markup=markup
    )
    await state.set_state(SendFSM.waiting_for_audience)


@router.message(Command("cancel"), IsAdmin())
async def cancel(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    await message.answer("Отменено", reply_markup=None)
    await state.clear()


@router.message(SendFSM.waiting_for_audience)
async def process_audience(message: Message, state: FSMContext):
    """Обработка выбора аудитории для рассылки"""
    audience = message.text.strip()
    
    if audience == "/cancel":
        await cancel(message, state)
        return
    
    if audience not in ["Всем пользователям", "Только активным", "Неактивным пользователям"]:
        await message.answer(
            "Пожалуйста, выберите одну из предложенных опций или отмените командой /cancel"
        )
        return
    
    await state.update_data(audience=audience)
    
    await message.answer(
        "Введите текст для рассылки. Поддерживается текст и одно вложение (фото, видео, документ).\n"
        "Для отмены используйте /cancel",
        reply_markup=None
    )
    await state.set_state(SendFSM.waiting_for_message)


@router.message(SendFSM.waiting_for_message)
async def send_message_(message: Message, state: FSMContext):
    """Отправка сообщения выбранной аудитории"""
    if not message.text and not message.photo and not message.video and not message.document:
        await message.answer("Сообщение должно содержать текст или вложение. Попробуйте еще раз.")
        return
    
    state_data = await state.get_data()
    audience_type = state_data.get("audience", "Всем пользователям")
    
    progress_message = await message.answer(f"Начинаю рассылку ({audience_type})...")
    await state.clear()

    try:
        today = date.today()
        
        if audience_type == "Всем пользователям":
            all_users = await Users.all()
        elif audience_type == "Только активным":
            # Пользователи с активной подпиской - у которых установлен expired_at, он в будущем,
            # и которые хотя бы раз подключались
            all_users = await Users.filter(
                is_registered=True, 
                expired_at__not_isnull=True, 
                expired_at__gt=today,
                connected_at__not_isnull=True
            )
        else:  # "Неактивным пользователям" - две категории: не подключались + истекшая подписка
            # 1. Пользователи, которые никогда не подключались
            never_connected_users = await Users.filter(
                connected_at__isnull=True
            )
            
            # 2. Пользователи с истекшей подпиской
            expired_users = await Users.filter(
                is_registered=True,
                expired_at__not_isnull=True,  # Добавляем проверку, что expired_at существует
                expired_at__lte=today
            )
            
            # Объединяем категории
            all_users = []
            all_users.extend(never_connected_users)
            all_users.extend(expired_users)
            
            # Логируем информацию о составе неактивных пользователей
            logger.info(
                f"Состав неактивных пользователей для рассылки: "
                f"никогда не подключались: {len(never_connected_users)}, "
                f"с истекшей подпиской: {len(expired_users)}, "
                f"всего: {len(all_users)}"
            )
            
        total_users = len(all_users)
        success, failure = 0, 0
        
        # Обновляем сообщение о прогрессе раз в 10 пользователей
        update_interval = max(1, min(10, total_users // 10))
        last_update = 0

        logger.info(f"Начало рассылки. Всего пользователей: {total_users}, тип аудитории: {audience_type}")
        
        for i, user in enumerate(all_users):
            try:
                await message.copy_to(user.id)
                success += 1
                # Для отладки
                logger.debug(f"Сообщение успешно отправлено пользователю {user.id}")
            except Exception as e:
                failure += 1
                error_message = str(e)
                logger.error(f"Ошибка при отправке сообщения пользователю {user.id}: {error_message}")
                if "Forbidden" in error_message:
                    logger.debug("Пользователь, вероятно, заблокировал бота")
            
            # Обновляем сообщение с прогрессом
            if (i + 1) % update_interval == 0 or i + 1 == total_users:
                progress_percent = round((i + 1) / total_users * 100)
                try:
                    await progress_message.edit_text(
                        f"Рассылка в процессе... {i+1}/{total_users} ({progress_percent}%)\n"
                        f"✅ Успешно: {success}\n"
                        f"❌ Ошибок: {failure}"
                    )
                    last_update = i + 1
                except Exception as e:
                    logger.error(f"Не удалось обновить сообщение о прогрессе: {e}")
            
            # Добавляем задержку, чтобы избежать ограничений API Telegram
            await asyncio.sleep(0.05)  # 50 мс

        final_message = (
            f"✅ Рассылка завершена!\n\n"
            f"📊 Статистика:\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Успешно отправлено: {success}\n"
            f"❌ Ошибок отправки: {failure}"
        )
        
        await progress_message.edit_text(final_message)
        logger.info(f"Рассылка завершена. Успешно: {success}, ошибок: {failure}")
        
    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"Критическая ошибка при рассылке: {e}\n{error_traceback}")
        await progress_message.edit_text(
            f"❌ Произошла ошибка при рассылке: {e}\n\n"
            f"Если ошибка повторяется, обратитесь к разработчику."
        )

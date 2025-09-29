from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bloobcat.db.prize_wheel import PrizeWheelHistory
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import telegram_settings, admin_settings


logger = get_logger("prize_wheel_notifications")


async def notify_prize_won(
    user_id: int,
    prize_type: str,
    prize_name: str,
    prize_value: str,
    history_id: int,
    requires_admin: bool,
    bot: Bot,
):
    try:
        user = await Users.get_or_none(id=user_id)
        if not user:
            logger.error(f"Пользователь {user_id} не найден для уведомления о призе")
            return

        await notify_logs_channel(user, prize_name, prize_value, bot)

        if requires_admin:
            await notify_admins_about_prize(user, prize_name, prize_value, history_id, bot)

        history_entry = await PrizeWheelHistory.get(id=history_id)
        if history_entry and requires_admin:
            await history_entry.mark_admin_notified()
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о призе: {e}")


async def notify_logs_channel(user: Users, prize_name: str, prize_value: str, bot: Bot):
    try:
        logs_channel = getattr(telegram_settings, "logs_channel", None) or getattr(
            admin_settings, "telegram_id", None
        )
        if not logs_channel:
            logger.warning("Канал логов не настроен")
            return

        message = (
            f"🎰 **Колесо призов**\n\n"
            f"👤 Пользователь: {user.name()}\n"
            f"🆔 ID: `{user.id}`\n"
            f"🏆 Приз: **{prize_name}**\n"
            f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )

        try:
            from bloobcat.bot.notifications.admin import write_to

            await bot.send_message(
                chat_id=logs_channel,
                text=message,
                reply_markup=await write_to(user.id),
                parse_mode="Markdown",
            )
        except Exception:
            await bot.send_message(
                chat_id=logs_channel, text=message, parse_mode="Markdown"
            )

        logger.info(f"Уведомление о призе {prize_name} отправлено в канал логов")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления в канал логов: {e}")


async def notify_admins_about_prize(
    user: Users, prize_name: str, prize_value: str, history_id: int, bot: Bot
):
    try:
        admins = await Users.filter(is_admin=True)
        if not admins:
            logger.warning("Админы не найдены")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=f"prize_confirm_prompt:{history_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"prize_reject_prompt:{history_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="📝 Написать", url=f"tg://user?id={user.id}"
                    )
                ],
            ]
        )

        message = (
            f"🎰 **Колесо призов - Требуется участие админа**\n\n"
            f"👤 Пользователь: {user.name()}\n"
            f"🆔 ID: `{user.id}`\n"
            f"🏆 Приз: **{prize_name}**\n"
            f"📅 Время выигрыша: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
            f"⚠️ Этот приз требует участия админа для выдачи."
        )

        for admin in admins:
            try:
                await bot.send_message(
                    chat_id=admin.id,
                    text=message,
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(
                    f"Ошибка при отправке уведомления админу {admin.id} (с кнопкой 'Написать'): {e}"
                )
                keyboard_no_write = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Подтвердить",
                                callback_data=f"prize_confirm_prompt:{history_id}",
                            ),
                            InlineKeyboardButton(
                                text="❌ Отклонить",
                                callback_data=f"prize_reject_prompt:{history_id}",
                            ),
                        ]
                    ]
                )
                try:
                    await bot.send_message(
                        chat_id=admin.id,
                        text=message,
                        reply_markup=keyboard_no_write,
                        parse_mode="Markdown",
                    )
                except Exception as e2:
                    logger.error(
                        f"Ошибка при отправке админу {admin.id} (без кнопки 'Написать'): {e2}"
                    )
                    try:
                        await bot.send_message(
                            chat_id=admin.id, text=message, parse_mode="Markdown"
                        )
                    except Exception as e3:
                        logger.error(
                            f"Повторная ошибка при отправке админу {admin.id} без кнопок: {e3}"
                        )

        logger.info(
            f"Уведомления о призе {prize_name} отправлены {len(admins)} админам"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомлений админам: {e}")


async def handle_prize_confirmation(
    admin_id: int, history_id: int, confirmed: bool, bot: Bot
):
    try:
        history_entry = await PrizeWheelHistory.get_or_none(id=history_id)
        if not history_entry:
            logger.error(f"Запись истории {history_id} не найдена")
            return

        user = await Users.get_or_none(id=history_entry.user_id)
        if not user:
            logger.error(f"Пользователь {history_entry.user_id} не найден")
            return

        # Блокируем повторную обработку
        if history_entry.is_claimed:
            await bot.send_message(
                chat_id=admin_id,
                text=f"ℹ️ Приз '{history_entry.prize_name}' для пользователя {user.name()} уже обработан ранее"
            )
            logger.info(f"Повторное нажатие подтверждения/отмены для history_id={history_id} проигнорировано")
            return

        if confirmed:
            # Начисляем эффект приза при подтверждении
            ptype = str(history_entry.prize_type)
            try:
                if ptype == "subscription":
                    days = int(str(history_entry.prize_value).strip())
                    await user.extend_subscription(days)
                elif ptype == "extra_spin":
                    user.prize_wheel_attempts = int(getattr(user, "prize_wheel_attempts", 0) or 0) + 1
                    await user.save()
            except Exception as e:
                logger.error(f"Ошибка начисления приза по подтверждению (history_id={history_id}): {e}")

            await history_entry.mark_as_claimed()
            if not history_entry.admin_notified:
                await history_entry.mark_admin_notified()
            await bot.send_message(
                chat_id=user.id,
                text=(
                    f"🎉 **Отличные новости!**\n\n"
                    f"🏆 Ваш приз **{history_entry.prize_name}** подтвержден!\n\n"
                    f"📞 С вами свяжутся для получения приза."
                ),
                parse_mode="Markdown",
            )
            await bot.send_message(
                chat_id=admin_id,
                text=f"✅ Приз {history_entry.prize_name} для пользователя {user.name()} подтвержден",
            )
            logger.info(
                f"Приз {history_entry.prize_name} для пользователя {user.id} подтвержден админом {admin_id}"
            )
        else:
            # Отмечаем как отклоненный
            try:
                if not history_entry.is_rejected:
                    await history_entry.mark_as_rejected()
            except Exception as e:
                logger.error(f"Ошибка пометки отклонения (history_id={history_id}): {e}")
            await bot.send_message(
                chat_id=user.id,
                text=(
                    f"😔 **К сожалению**\n\n"
                    f"🏆 Ваш приз **{history_entry.prize_name}** был отклонен.\n\n"
                    f"📞 Обратитесь в поддержку для уточнения деталей."
                ),
                parse_mode="Markdown",
            )
            await bot.send_message(
                chat_id=admin_id,
                text=f"❌ Приз {history_entry.prize_name} для пользователя {user.name()} отклонен",
            )
            if not history_entry.admin_notified:
                await history_entry.mark_admin_notified()
            logger.info(
                f"Приз {history_entry.prize_name} для пользователя {user.id} отклонен админом {admin_id}"
            )
    except Exception as e:
        logger.error(f"Ошибка при обработке подтверждения приза: {e}")



async def notify_spin_awarded(user_id: int, added_attempts: int, total_attempts: int, bot: Bot):
    try:
        user = await Users.get_or_none(id=user_id)
        if not user:
            logger.error(f"Пользователь {user_id} не найден для уведомления о начислении круток")
            return

        plural = "крутка" if int(added_attempts) == 1 else "крутки"
        message = (
            f"🎰 Начислены {added_attempts} {plural} за автопродление.\n"
            f"Доступно попыток: {total_attempts}"
        )

        await bot.send_message(chat_id=user_id, text=message)
        logger.info(
            f"Отправлено уведомление о начислении круток пользователю {user_id}: +{added_attempts}, всего {total_attempts}"
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о начислении круток пользователю {user_id}: {e}")


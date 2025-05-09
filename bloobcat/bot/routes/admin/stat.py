from datetime import datetime, timedelta
import logging

from aiogram.dispatcher.router import Router
from aiogram.filters.command import Command, CommandObject
from aiogram.types.message import Message
from pytz import UTC
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bloobcat.bot.routes.admin.functions import IsPartnerOrAdmin
from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("stat"), IsPartnerOrAdmin())
async def admin_stat(message: Message, command: CommandObject):
    utm = command.args
    amount = await Users.filter(utm=utm).count()
    registered = await Users.filter(utm=utm, is_registered=True).count()
    payed = 0
    # Count how many registered users have at least one successful payment
    registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
    if registered_ids:
        paid_user_ids = await ProcessedPayments.filter(
            user_id__in=registered_ids, status="succeeded"
        ).values_list("user_id", flat=True)
        payed = len(set(paid_user_ids))
    else:
        payed = 0

    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    percent_registered = amount and registered / amount * 100 or 0
    percent_payed = registered and payed / registered * 100 or 0

    stats_message = (
        f"📊 <b>Статистика по UTM:</b> <code>{utm}</code>\n"
        f"<i>отчет на {now}</i>\n\n"
        f"👥 Всего зашли: <b>{amount}</b>\n"
        f"✅ Активировали: <b>{registered}</b> (<i>{percent_registered:.1f}%</i>)\n"
        f"💰 Оплатили: <b>{payed}</b> (<i>{percent_payed:.1f}%</i>)"
    )
    await message.answer(stats_message, parse_mode="HTML")


@router.message(Command("online"), IsPartnerOrAdmin())
async def online_(message: Message):
    m = await message.answer("⏳ <i>Подождите...</i>", parse_mode="HTML")

    active_users = await Users.filter(
        connected_at__gte=datetime.now(UTC) - timedelta(minutes=15)
    )
    i = len(active_users)

    await m.edit_text(f"👥 <b>Сейчас онлайн:</b> <code>{i}</code>", parse_mode="HTML")


@router.message(Command("stats"), IsPartnerOrAdmin())
async def stats_all(message: Message):
    await show_utm_list(message, page=0)


async def show_utm_list(message, page=0, edit_message=False):
    """Показывает список UTM с пагинацией"""
    # Получаем все UTM, включая None
    raw_utms = await Users.all().values_list("utm", flat=True)
    
    # Фильтруем None и пустые строки, получаем уникальные значения
    utms = list(set([utm for utm in raw_utms if utm is not None and utm != ""]))
    logger.debug(f"UTMs после фильтрации: {utms}")
    
    if not utms:
        return await message.answer("Нет зарегистрированных UTM", parse_mode="HTML")
    
    # Сортируем UTM для консистентности
    utms.sort()
    
    # Считаем общую статистику (включая пользователей без UTM)
    total_users = await Users.all().count()
    total_registered = await Users.filter(is_registered=True).count()
    
    # Получаем количество пользователей онлайн
    now_utc = datetime.now(UTC) # Get current UTC time
    active_users_online = await Users.filter(
        connected_at__gte=now_utc - timedelta(minutes=15)
    ).count()
    
    # Получаем все ID зарегистрированных пользователей
    registered_ids_all = await Users.filter(is_registered=True).values_list("id", flat=True)
    total_paid = 0
    if registered_ids_all:
        paid_user_ids_all = await ProcessedPayments.filter(
            user_id__in=registered_ids_all, status="succeeded"
        ).values_list("user_id", flat=True)
        total_paid = len(set(paid_user_ids_all))
    
    # Считаем активных пользователей (подписка не истекла)
    total_active_now = await Users.filter(is_registered=True, expired_at__gt=now_utc).count()
    
    now_str = now_utc.strftime("%d.%m.%Y %H:%M:%S") # Use formatted UTC time for report time
    percent_registered_total = total_users and total_registered / total_users * 100 or 0
    percent_paid_total = total_registered and total_paid / total_registered * 100 or 0
    percent_active_now = total_registered and total_active_now / total_registered * 100 or 0 # Calculate percentage of active users
    
    # Создаем сообщение с общей статистикой
    lines = [
        f"📊 <b>Общая статистика:</b> <i>отчет на {now_str}</i>\n",
        f"👥 Всего пользователей: <b>{total_users}</b>",
        f"✅ Активировано: <b>{total_registered}</b> (<i>{percent_registered_total:.1f}%</i>)",
        f"⚡ Активны сейчас: <b>{total_active_now}</b> (<i>{percent_active_now:.1f}%)</i>", # Add new line for active users
        f"💰 Оплачено: <b>{total_paid}</b> (<i>{percent_paid_total:.1f}%</i>)",
        f"🟢 Сейчас онлайн: <b>{active_users_online}</b>" # Use correct variable name
    ]
    
    # Определяем пагинацию
    items_per_page = 5  # Показываем 5 UTM на странице
    total_pages = (len(utms) + items_per_page - 1) // items_per_page
    
    # Ограничиваем номер страницы
    page = max(0, min(page, total_pages - 1))
    
    # Получаем UTM для текущей страницы
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, len(utms))
    current_page_utms = utms[start_idx:end_idx]
    
    # Строим клавиатуру пагинации
    builder = InlineKeyboardBuilder()
    
    # Кнопки для UTM на текущей странице с информацией о статистике
    for utm in current_page_utms:
        # Получаем статистику для этого UTM для отображения в кнопке
        amount = await Users.filter(utm=utm).count()
        registered = await Users.filter(utm=utm, is_registered=True).count()
        
        # Считаем оплативших для UTM
        payed = 0
        registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
        if registered_ids:
            paid_user_ids = await ProcessedPayments.filter(
                user_id__in=registered_ids, status="succeeded"
            ).values_list("user_id", flat=True)
            payed = len(set(paid_user_ids))

        # Считаем активных сейчас для UTM
        active_now_utm = await Users.filter(utm=utm, is_registered=True, expired_at__gt=now_utc).count()
        
        utm_str = str(utm)
        # Обновляем текст кнопки, добавляя активных
        button_text = f"{utm_str} ({amount}|{registered}|{active_now_utm}|{payed})" # Add active_now_utm
        # Добавляем каждую кнопку в отдельную строку
        builder.row(InlineKeyboardButton(text=button_text, callback_data=f"utm:{utm_str}:{page}"))
    
    # Кнопки навигации (предыдущая, номер страницы, следующая)
    if total_pages > 1:
        nav_row = []
        
        # Кнопка "Назад"
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                text="◀️ Назад", 
                callback_data=f"page_{page-1}"
            ))
        
        # Индикатор страницы
        nav_row.append(InlineKeyboardButton(
            text=f"{page+1}/{total_pages}", 
            callback_data="noop"
        ))
        
        # Кнопка "Вперед"
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                text="Вперед ▶️", 
                callback_data=f"page_{page+1}"
            ))
        
        builder.row(*nav_row)
    
    # Соединяем сообщение и отправляем
    message_text = "\n".join(lines)
    
    if edit_message and hasattr(message, 'edit_text'):
        try:
            await message.edit_text(
                message_text, 
                parse_mode="HTML", 
                reply_markup=builder.as_markup()
            )
        except Exception as e:
            logger.error(f"Ошибка при редактировании сообщения: {e}")
            # Если не удалось отредактировать, отправляем новое
            await message.answer(
                message_text, 
                parse_mode="HTML", 
                reply_markup=builder.as_markup()
            )
    else:
        await message.answer(
            message_text, 
            parse_mode="HTML", 
            reply_markup=builder.as_markup()
        )


async def show_utm_detail(message, utm, page=0):
    """Показывает детальную статистику по конкретному UTM"""
    # Compute stats for specific UTM
    amount = await Users.filter(utm=utm).count()
    registered = await Users.filter(utm=utm, is_registered=True).count()
    registered_ids = await Users.filter(utm=utm, is_registered=True).values_list("id", flat=True)
    
    payed = 0
    if registered_ids:
        paid_user_ids = await ProcessedPayments.filter(
            user_id__in=registered_ids, status="succeeded"
        ).values_list("user_id", flat=True)
        payed = len(set(paid_user_ids))

    # Count currently active users for this UTM
    now_utc = datetime.now(UTC)
    active_now = await Users.filter(utm=utm, is_registered=True, expired_at__gt=now_utc).count()
    
    now_str = now_utc.strftime("%d.%m.%Y %H:%M:%S") # Use formatted UTC time for report time
    percent_registered = amount and registered / amount * 100 or 0
    percent_payed = registered and payed / registered * 100 or 0
    percent_active_now = registered and active_now / registered * 100 or 0 # Calculate percentage of active users
    
    stats_message = (
        f"📊 <b>Статистика по UTM:</b> <code>{utm}</code>\n"
        f"<i>отчет на {now_str}</i>\n\n"
        f"👥 Всего зашли: <b>{amount}</b>\n"
        f"✅ Активировали: <b>{registered}</b> (<i>{percent_registered:.1f}%</i>)\n"
        f"⚡ Активны сейчас: <b>{active_now}</b> (<i>{percent_active_now:.1f}%)</i>\n"  # Add new line
        f"💰 Оплатили: <b>{payed}</b> (<i>{percent_payed:.1f}%</i>)"
    )
    
    # Создаем кнопку "Назад" к списку UTM
    builder = InlineKeyboardBuilder()
    builder.button(
        text="◀️ Вернуться к списку", 
        callback_data=f"back_to_list_{page}"
    )
    
    try:
        await message.edit_text(
            stats_message, 
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        logger.error(f"Ошибка при редактировании сообщения детальной статистики: {e}")
        await message.answer(
            stats_message, 
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )


@router.callback_query(lambda c: c.data and c.data.startswith(("utm:", "page_", "back_to_list_", "noop")), IsPartnerOrAdmin())
async def callback_handler(callback: CallbackQuery):
    # Acknowledge callback to remove loading spinner
    await callback.answer()
    
    if callback.data == "noop":
        await callback.answer("Текущая страница")
        return
        
    # Обработка навигации по страницам
    if callback.data.startswith("page_"):
        try:
            page = int(callback.data[len("page_"):])
            await show_utm_list(callback.message, page=page, edit_message=True)
            return
        except ValueError:
            logger.error(f"Неверный формат страницы: {callback.data}")
    
    # Обработка кнопки "назад к списку"
    if callback.data.startswith("back_to_list_"):
        try:
            page = int(callback.data[len("back_to_list_"):])
            await show_utm_list(callback.message, page=page, edit_message=True)
            return
        except ValueError:
            logger.error(f"Неверный формат страницы для возврата: {callback.data}")
    
    # Обработка нажатия на UTM
    if callback.data.startswith("utm:"):
        parts = callback.data.split(":", 2)
        if len(parts) == 3:
            utm = parts[1]
            page = int(parts[2])
            await show_utm_detail(callback.message, utm, page)
            return
        
    # Если ничего не сработало
    logger.error(f"Неизвестный формат callback_data: {callback.data}")
    await callback.answer("Неизвестная команда")

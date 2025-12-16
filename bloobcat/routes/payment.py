from random import randint
from datetime import date, datetime, timedelta
import json
import asyncio
import random
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Header, Request # type: ignore
from yookassa import Configuration, Payment, Webhook
from yookassa.domain.notification import WebhookNotification, WebhookNotificationEventType
from urllib3.exceptions import ConnectTimeoutError, ReadTimeoutError
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout

from bloobcat.bot.bot import get_bot_username
from bloobcat.bot.notifications.admin import on_payment, cancel_subscription
from bloobcat.bot.notifications.general.referral import on_referral_payment
from bloobcat.bot.notifications.subscription.renewal import (
    notify_auto_renewal_success_balance,
    notify_auto_renewal_failure,
    notify_renewal_success_yookassa
)
from bloobcat.bot.notifications.prize_wheel import notify_spin_awarded
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users, normalize_date
from bloobcat.funcs.validate import validate
from bloobcat.settings import yookassa_settings, remnawave_settings
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_payment_logger
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.hwid_utils import cleanup_user_hwid_devices
from bloobcat.services.discounts import (
    apply_personal_discount,
    consume_discount_if_needed,
)
from bloobcat.utils.dates import add_months_safe

# Инициализируем клиент ЮKассы
Configuration.account_id = yookassa_settings.shop_id
Configuration.secret_key = yookassa_settings.secret_key.get_secret_value()

router = APIRouter(prefix="/pay", tags=["pay"])
logger = get_payment_logger()

@router.get("/tariffs")
async def get_tariffs():
    return await Tariffs.all().order_by("order")



@router.post("/webhook/yookassa/{secret}")
async def yookassa_webhook(request: Request, secret: str):
    if secret != yookassa_settings.webhook_secret:
        logger.error("Получен webhook с неверным секретным ключом")
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    try:
        # Получаем тело запроса
        body = await request.json()
        # Получаем заголовки
        headers = dict(request.headers)
        
        # Проверяем подпись и создаем объект уведомления
        notification = WebhookNotification(body, headers)
        
        event = notification.event
        payment = notification.object
        
        # Логируем событие
        logger.info(
            f"Получен webhook от YooKassa: {event}",
            extra={
                'payment_id': payment.id if payment else 'unknown',
                'user_id': payment.metadata.get("user_id", "unknown") if payment else "unknown",
                'amount': payment.amount.value if payment else "unknown",
                'status': payment.status if payment else "unknown"
            }
        )
        
        try:
            data = payment.metadata
            user = await Users.get(id=data["user_id"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректные метаданные в webhook'е YooKassa: {e}",
                extra={
                    'payment_id': payment.id if payment else 'unknown',
                    'user_id': "unknown",
                    'amount': payment.amount.value if payment else "unknown",
                    'status': payment.status if payment else "unknown"
                }
            )
            return {"status": "error", "message": "Invalid metadata"}
        except Exception as e:
            logger.error(
                f"Ошибка при получении пользователя в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id if payment else 'unknown'}
            )
            return {"status": "error", "message": "User not found"}

        # Вычисляем will_retry для уведомлений об ошибках
        user_expired_at = normalize_date(user.expired_at)
        will_retry = user_expired_at is not None and (user_expired_at - date.today()).days >= 0

        # Проверяем, не обработан ли уже этот платеж
        if not payment.id:
            logger.error(
                "Отсутствует payment_id в webhook'е YooKassa",
                extra={'payment_id': 'missing'}
            )
            return {"status": "error", "message": "Missing payment_id"}

        processed_payment = await ProcessedPayments.get_or_none(payment_id=payment.id)
        if processed_payment:
            logger.info(
                f"Платеж {payment.id} уже был обработан ранее",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': processed_payment.status
                }
            )
            return {"status": "ok"}
        
        # Обработка разных типов событий
        if event == WebhookNotificationEventType.REFUND_SUCCEEDED:
            # При возврате средств отключаем автопродление
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Резервации отключены
            
            # Сохраняем информацию о возврате
            await ProcessedPayments.create(
                payment_id=payment.id,
                user_id=user.id,
                amount=float(payment.amount.value),
                amount_external=float(payment.amount.value),
                amount_from_balance=0,
                status="refunded"
            )
            
            logger.info(
                f"Автопродление отключено для пользователя {user.id} из-за возврата средств",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': "refunded"
                }
            )
            return {"status": "ok"}
        
        if event == WebhookNotificationEventType.PAYMENT_CANCELED:
            # Сохраняем информацию об отмене
            await ProcessedPayments.create(
                payment_id=payment.id,
                user_id=user.id,
                amount=float(payment.amount.value),
                amount_external=float(payment.amount.value),
                amount_from_balance=0,
                status="canceled"
            )
            # Резервации отключены
            
            logger.info(
                f"Автопродление отключено для пользователя {user.id} из-за отмены платежа",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': "canceled"
                }
            )
            if data.get("is_auto", False):
                disable = data.get("disable_on_fail", False)
                if disable:
                    user.is_subscribed = False
                    user.renew_id = None
                    await user.save()
                    # Уведомляем админа об отключении автопродления из-за отмены платежа
                    await cancel_subscription(user, reason="Автоплатеж был отменен")
                await notify_auto_renewal_failure(user, reason="Платеж был отменен", will_retry=will_retry)
            return {"status": "ok"}
        
        if event != WebhookNotificationEventType.PAYMENT_SUCCEEDED:
            return {"status": "ok"}
        
        if payment.status != "succeeded":
            logger.warning(f"Автоплатеж {payment.id} для пользователя {user.id} завершился со статусом {payment.status}")
            if data.get("is_auto", False):
                disable = data.get("disable_on_fail", False)
                if disable:
                    user.is_subscribed = False
                    user.renew_id = None
                    await user.save()
                    # Уведомляем админа об отключении автопродления из-за неуспешного платежа
                    await cancel_subscription(user, reason=f"Автоплатеж завершился со статусом: {payment.status}")
                await notify_auto_renewal_failure(user, reason=f"Платеж не прошел (статус: {payment.status})", will_retry=will_retry)
            return {"status": "ok"}
        
        try:
            months = int(data["month"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректное значение месяцев в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id}
            )
            return {"status": "error", "message": "Invalid month value"}
        
        # Достаём скидку, применённую при создании платежа (если была)
        discount_id = data.get("discount_id")
        discount_percent = int(data.get("discount_percent") or 0)

        # Сразу пытаемся списать скидку: если списалась, пропорциональная коррекция не нужна
        consumed = False
        try:
            consumed = await consume_discount_if_needed(discount_id)
        except Exception:
            consumed = False

        # Новый тариф из webhook
        tariff_id = data.get("tariff_id")
        if tariff_id is not None:
            # Получаем новый тариф
            new_tariff = await Tariffs.get_or_none(id=tariff_id)
            if not new_tariff:
                logger.error(f"Не найден тариф {tariff_id} при обработке платежа")
                return {"status": "error", "message": "Tariff not found"}
                
            # Проверяем, есть ли у пользователя активная подписка и активный тариф
            current_date = date.today()
            additional_days = 0
            user_expired_at = normalize_date(user.expired_at)

            if user_expired_at and user_expired_at > current_date and user.active_tariff_id:
                # У пользователя есть действующая подписка
                try:
                    # Получаем активный тариф
                    active_tariff = await ActiveTariffs.get(id=user.active_tariff_id)
                    
                    # Вычисляем оставшиеся дни подписки
                    days_remaining = (user_expired_at - current_date).days
                    logger.info(f"У пользователя {user.id} осталось {days_remaining} дней подписки")
                    
                    # Рассчитываем количество дней, которое давал старый тариф
                    old_months = int(active_tariff.months)
                    old_target_date = add_months_safe(current_date, old_months)
                    old_total_days = (old_target_date - current_date).days
                    
                    # Рассчитываем процент неиспользованной подписки
                    unused_percent = days_remaining / old_total_days if old_total_days > 0 else 0
                    unused_value = unused_percent * active_tariff.price
                    
                    logger.info(
                        f"Неиспользованная часть подписки пользователя {user.id}: " 
                        f"{days_remaining}/{old_total_days} дней ({unused_percent:.2%}), " 
                        f"стоимость: {unused_value:.2f} руб."
                    )
                    
                    # ИСПРАВЛЕННАЯ ЛОГИКА: рассчитываем через пропорцию от общей суммы
                    # Выполняем ТОЛЬКО если скидка не была списана (например, повторная оплата без скидки)
                    if new_tariff.price > 0 and not consumed:
                        # Получаем сумму, которую заплатил пользователь
                        amount_paid_by_user = float(payment.amount.value)
                        amount_from_balance = float(data.get("amount_from_balance", 0))
                        total_paid = amount_paid_by_user + amount_from_balance
                        
                        # Получаем device_count и рассчитываем правильную цену
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1
                        
                        # Рассчитываем итоговую цену для указанного количества устройств
                        correct_new_tariff_price = new_tariff.calculate_price(device_count)
                        
                        # Общая сумма = заплачено пользователем + компенсация за старый тариф
                        total_amount = total_paid + unused_value
                        
                        # Рассчитываем новый период подписки (стандартный для тарифа)
                        tariff_months = int(new_tariff.months)
                        new_target_date = add_months_safe(current_date, tariff_months)
                        new_total_days = (new_target_date - current_date).days
                        
                        # Пропорция: x дней / общая_сумма = полный_период_тарифа / цена_тарифа
                        # x = общая_сумма * полный_период_тарифа / цена_тарифа
                        calculated_days = int(total_amount * new_total_days / correct_new_tariff_price)
                        
                        logger.info(
                            f"ИСПРАВЛЕННЫЙ расчёт для пользователя {user.id}: "
                            f"Заплачено: {total_paid:.2f} руб + Компенсация: {unused_value:.2f} руб = "
                            f"Общая сумма: {total_amount:.2f} руб. "
                            f"Пропорция: {calculated_days} дней = {total_amount:.2f} * {new_total_days} / {correct_new_tariff_price:.2f}"
                        )
                        
                        # Устанавливаем рассчитанные дни как итоговые (без additional_days)
                        additional_days = 0  # Сбрасываем, так как используем calculated_days
                        days = calculated_days  # Переопределяем days
                except Exception as e:
                    logger.error(f"Ошибка при расчете переноса подписки для {user.id}: {str(e)}")
                    additional_days = 0  # При ошибке не добавляем дополнительные дни
            
            # Рассчитываем точное количество дней для указанного количества месяцев
            # При смене тарифа days уже рассчитано через пропорцию
            if 'calculated_days' not in locals():
                # Обычная покупка нового тарифа без смены
                current_date = date.today()
                target_date = add_months_safe(current_date, months)
                days = (target_date - current_date).days
                logger.info(f"Стандартное количество дней подписки: {days}")
            else:
                # days уже рассчитано через пропорцию при смене тарифа
                logger.info(f"Итоговое количество дней подписки (через пропорцию): {days}")
        else:
            # Если нет tariff_id, значит это автоплатеж или другой тип платежа, просто рассчитываем дни как обычно
            current_date = date.today()
            target_date = add_months_safe(current_date, months)
            days = (target_date - current_date).days
            logger.info(f"Стандартное количество дней подписки: {days}")
        
        # Рассчитываем точное количество дней для указанного количества месяцев
        try:
            amount_from_balance = float(data.get("amount_from_balance", 0))
            if amount_from_balance > 0:
                initial_balance = user.balance
                user.balance = max(0, user.balance - amount_from_balance)
                logger.info(
                    f"Списание с бонусного баланса пользователя {user.id}. "
                    f"Сумма: {amount_from_balance}. Баланс до: {initial_balance}, После: {user.balance}",
                    extra={
                        'payment_id': payment.id,
                        'user_id': user.id,
                        'amount_from_balance': amount_from_balance
                    }
                )
            
            # Устанавливаем новую дату окончания подписки
            # В случае автопродления переходим на новый тариф, сбрасывая старую подписку
            is_auto = data.get("is_auto", False)
            if is_auto:
                # Для автопродления используем extend_subscription вместо прямой установки даты
                await user.extend_subscription(days)
                logger.info(
                    f"Автопродление: подписка пользователя {user.id} продлена на {days} дней, новая дата истечения: {user.expired_at}"
                )
            else:
                # При смене тарифа (когда calculated_days определено) устанавливаем от текущей даты
                # чтобы избежать двойного учёта компенсации
                if 'calculated_days' in locals():
                    # Смена тарифа с компенсацией - устанавливаем от текущей даты
                    user.expired_at = current_date + timedelta(days=days)
                    logger.info(
                        f"Смена тарифа для пользователя {user.id}: установлена дата {user.expired_at} "
                        f"({days} дней от текущей даты, рассчитано через пропорцию)"
                    )
                else:
                    # Обычное продление или новая подписка без смены тарифа
                    await user.extend_subscription(days)
                    logger.info(
                        f"Подписка пользователя {user.id} продлена на {days} дней, новая дата истечения: {user.expired_at} "
                        f"(с учетом оставшихся дней предыдущей подписки/триала)"
                    )
                
            # If a tariff_id is provided in metadata, ensure it's created in ActiveTariffs and assign to user
            if tariff_id is not None:
                original = await Tariffs.get_or_none(id=tariff_id)
                if original:
                    # Получаем device_count из метаданных платежа
                    try:
                        device_count = int(data.get("device_count", 1))
                    except (ValueError, TypeError):
                        device_count = 1
                    if device_count < 1:
                        device_count = 1
                    
                    # Рассчитываем итоговую цену для указанного количества устройств
                    calculated_price = original.calculate_price(device_count)
                    
                    # Удаляем предыдущий активный тариф, если он есть
                    if user.active_tariff_id:
                        old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
                        if old_active_tariff:
                            logger.info(f"Удаляем предыдущий активный тариф {user.active_tariff_id} пользователя {user.id}")
                            await old_active_tariff.delete()
                        else:
                            logger.warning(f"Не найден активный тариф {user.active_tariff_id} для удаления у пользователя {user.id}")
                    
                    # код по сбросу HWID временно отключен
                    # if user.remnawave_uuid:
                        # await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)
                    
                    # Create a new active tariff entry with random ID
                    active_tariff = await ActiveTariffs.create(
                        user=user,  # Link to this user
                        name=original.name,
                        months=original.months,
                        price=calculated_price,  # Используем рассчитанную цену
                        hwid_limit=device_count,  # Используем выбранное количество устройств
                        progressive_multiplier=original.progressive_multiplier,
                        residual_day_fraction=0.0
                    )
                    # Link user to this active tariff
                    user.active_tariff_id = active_tariff.id

                    # Устанавливаем hwid_limit пользователю из выбранного количества устройств
                    user.hwid_limit = device_count
                    logger.info(f"Created ActiveTariff {active_tariff.id} for user {user.id} based on tariff {original.id}, device_count={device_count}, установлен hwid_limit={device_count}")

                    # ВАЖНО: сохраняем active_tariff_id и hwid_limit в БД как можно раньше
                    # чтобы минимизировать race condition с remnawave_updater
                    try:
                        await user.save(update_fields=["active_tariff_id", "hwid_limit"])
                        logger.debug(f"Ранее сохранены active_tariff_id={active_tariff.id} и hwid_limit={device_count} для пользователя {user.id}")
                    except Exception as persist_exc:
                        logger.warning(f"Не удалось рано сохранить active_tariff_id/hwid_limit для {user.id}: {persist_exc}")
                else:
                    logger.error(f"Original tariff {tariff_id} not found; skipping ActiveTariffs")
            
            # После успешной оплаты сбрасываем счётчик уменьшений лимита устройств
            if user.active_tariff_id:
                await ActiveTariffs.filter(id=user.active_tariff_id).update(devices_decrease_count=0)
            
            # Синхронизируем данные с RemnaWave
            if user.remnawave_uuid:
                # Настройки бесконечных повторных попыток с ограничением по времени
                max_total_time = 60  # Максимальное время в секундах для всех попыток (1 минута)
                start_time = datetime.now()
                retry_count = 0
                remnawave_client = None
                success = False
                
                try:
                    # Подготавливаем параметры для обновления
                    update_params = {}

                    # Передаём дату в формате date; клиент внутри сам форматирует expireAt
                    update_params["expireAt"] = user.expired_at
                    
                    # Определяем hwid_limit ТОЛЬКО для новых подписок (когда есть tariff_id),
                    # при автопродлении hwid_limit не меняем
                    hwid_limit = None
                    if tariff_id is not None and original:
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1
                        hwid_limit = device_count
                        logger.info(f"Новая подписка: устанавливаем hwid_limit={hwid_limit} из device_count для тарифа ID={original.id}")
                        update_params["hwidDeviceLimit"] = hwid_limit
                    else:
                        logger.info(f"Автопродление: hwid_limit не меняем, обновляем только дату истечения")
                    
                    # Цикл повторных попыток обновления информации в RemnaWave
                    while not success:
                        # Проверяем, не превысили ли мы общее время попыток
                        elapsed_time = (datetime.now() - start_time).total_seconds()
                        if elapsed_time > max_total_time:
                            logger.error(
                                f"Превышено максимальное время ({max_total_time} сек) для обновления пользователя {user.id} в RemnaWave. "
                                f"Выполнено {retry_count} попыток за {elapsed_time:.1f} сек."
                            )
                            break
                            
                        try:
                            retry_count += 1
                            
                            # Создаем клиент RemnaWave для каждой попытки
                            if remnawave_client:
                                await remnawave_client.close()
                            remnawave_client = RemnaWaveClient(
                                remnawave_settings.url, 
                                remnawave_settings.token.get_secret_value()
                            )
                            
                            # Обновляем пользователя в RemnaWave
                            logger.info(
                                f"Попытка #{retry_count} [{elapsed_time:.1f} сек]: Обновляем пользователя {user.id} в RemnaWave (UUID: {user.remnawave_uuid}). "
                                f"Новая дата: {user.expired_at}" + 
                                (f", hwid_limit: {hwid_limit}" if hwid_limit is not None else ", hwid_limit без изменений")
                            )
                            
                            try:
                                await remnawave_client.users.update_user(
                                    uuid=user.remnawave_uuid,
                                    **update_params
                                )
                            except Exception as update_err:
                                # Если юзер удален в RemnaWave – пересоздаём и пытаемся снова
                                if any(token in str(update_err) for token in ["User not found", "A039", "Update user error"]):
                                    recreated = await user.recreate_remnawave_user()
                                    if recreated and user.remnawave_uuid:
                                        await remnawave_client.users.update_user(
                                            uuid=user.remnawave_uuid,
                                            **update_params
                                        )
                                else:
                                    raise
                            
                            logger.info(f"УСПЕХ! Пользователь {user.id} обновлен в RemnaWave с попытки #{retry_count} за {elapsed_time:.1f} сек")
                            success = True
                            break  # Успешное обновление, выходим из цикла
                            
                        except Exception as retry_exc:
                            # Ограничиваем экспоненциальный рост задержки
                            backoff_time = min(10, 0.5 * (2 ** min(retry_count, 5)) + random.uniform(0, 0.5))
                            logger.warning(
                                f"Ошибка при обновлении пользователя {user.id} в RemnaWave (попытка {retry_count}, прошло {elapsed_time:.1f} сек): {str(retry_exc)}. "
                                f"Повторная попытка через {backoff_time:.2f} сек."
                            )
                            await asyncio.sleep(backoff_time)
                    
                    # Если не удалось обновить после всех попыток
                    if not success:
                        logger.error(
                            f"НЕ УДАЛОСЬ обновить пользователя {user.id} в RemnaWave даже после {retry_count} попыток. "
                            f"Общее время: {(datetime.now() - start_time).total_seconds():.1f} сек."
                        )
                    
                except Exception as e:
                    logger.error(f"Ошибка при обновлении пользователя {user.id} в RemnaWave: {str(e)}")
                    # Продолжаем обработку платежа, несмотря на ошибку синхронизации с RemnaWave
                finally:
                    # Закрываем клиент в любом случае
                    if remnawave_client:
                        try:
                            await remnawave_client.close()
                        except Exception as close_exc:
                            logger.warning(f"Ошибка при закрытии клиента RemnaWave: {str(close_exc)}")

            # Если это автоплатеж и он успешен, обновляем статус подписки
            if payment.payment_method.saved and not is_auto:
                user.renew_id = payment.payment_method.id
                user.is_subscribed = True
            
            # Если это автоплатеж и он успешен, обновляем статус подписки
            if is_auto and payment.status == "succeeded":
                user.is_subscribed = True
            
            # Если у пользователя был пробный период, сбрасываем флаг
            if user.is_trial:
                user.is_trial = False
                logger.info(
                    f"Сброшен флаг пробного периода для пользователя {user.id} после оплаты подписки",
                    extra={
                        'payment_id': payment.id,
                        'user_id': user.id
                    }
                )
            
            await user.save()  # Сохраняем пользователя (включая обновленный баланс)
            
            amount_paid_via_yookassa = float(payment.amount.value)
            full_tariff_price_for_history = amount_paid_via_yookassa + amount_from_balance
            
            # Сохраняем информацию об успешном платеже
            await ProcessedPayments.create(
                payment_id=payment.id,
                user_id=user.id,
                amount=full_tariff_price_for_history, # Используем полную стоимость тарифа
                amount_external=amount_paid_via_yookassa,
                amount_from_balance=amount_from_balance,
                status="succeeded"
            )
            
            logger.info(
                f"Успешно продлена подписка для пользователя {user.id} на {days} дней",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value, # Сумма платежа Yookassa
                    'amount_from_balance': amount_from_balance, # Сумма списания с баланса
                    'status': "succeeded",
                    'is_auto': is_auto,
                    'discount_percent': discount_percent,
                    'discount_id': discount_id,
                }
            )

            # Списываем использование скидки напрямую, если не списали ранее
            if not consumed:
                try:
                    consumed = await consume_discount_if_needed(discount_id)
                except Exception:
                    consumed = False

            # Если скидка не была списана (например, второй платёж без доступной скидки),
            # корректируем дни пропорционально фактически оплаченной сумме
            if not consumed and tariff_id is not None:
                try:
                    original = await Tariffs.get_or_none(id=tariff_id)
                    if original:
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1
                        correct_new_tariff_price = original.calculate_price(device_count)
                        amount_paid_by_user = float(payment.amount.value)
                        amount_from_balance = float(data.get("amount_from_balance", 0))
                        total_paid_now = amount_paid_by_user + amount_from_balance
                        current_date = date.today()
                        original_months = int(original.months)
                        new_target_date = add_months_safe(current_date, original_months)
                        new_total_days = (new_target_date - current_date).days
                        proportional_days = int(total_paid_now * new_total_days / max(1, correct_new_tariff_price))
                        # Берём минимум, чтобы не подарить лишние дни
                        days = min(days, proportional_days)
                except Exception:
                    pass
            
            # Уведомляем пользователя об успешном продлении, ТОЛЬКО ЕСЛИ это был автоплатеж
            if is_auto:
                # Начисление круток за автосписание: 1 крутка за каждый месяц
                try:
                    attempts_before = int(getattr(user, "prize_wheel_attempts", 0) or 0)
                    if months and months > 0:
                        user.prize_wheel_attempts = attempts_before + int(months)
                        await user.save()
                        logger.info(
                            f"Начислено {months} круток за автосписание пользователю {user.id}. Было: {attempts_before}, стало: {user.prize_wheel_attempts}"
                        )
                except Exception as award_exc:
                    logger.error(f"Не удалось начислить крутки за автосписание для {user.id}: {award_exc}")
                try:
                    await notify_renewal_success_yookassa(
                        user=user,
                        days=days,
                        amount_paid_via_yookassa=amount_paid_via_yookassa,
                        amount_from_balance=amount_from_balance
                    )
                except Exception as notify_exc:
                     logger.error(f"Ошибка при отправке уведомления об успешном АВТОпродлении для {user.id}: {notify_exc}")
                # Сообщение пользователю о начислении круток
                try:
                    await notify_spin_awarded(user=user, added_attempts=int(months), total_attempts=int(user.prize_wheel_attempts or 0))
                except Exception as e_notify_spins:
                    logger.error(f"Ошибка уведомления о крутках (вебхук) для {user.id}: {e_notify_spins}")
            
        except Exception as e:
            logger.error(
                f"Ошибка при продлении подписки в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id}
            )
            return {"status": "error", "message": "Error extending subscription"}

        amount = payment.amount.value
        referrer = None

        if user.referred_by:
            try:
                referrer = await Users.get(id=user.referred_by)
                await on_referral_payment(
                    referrer,
                    user,
                    amount,
                )
            except Exception as e:
                logger.error(
                    f"Ошибка при обработке реферала в webhook'е YooKassa: {e}",
                    extra={'payment_id': payment.id}
                )

        try:
            await on_payment(
                user_id=user.id,
                is_sub=user.is_subscribed,
                referrer=referrer.name() if referrer else None,
                amount=amount,
                months=months,
                method="yookassa",
                payment_id=payment.id,
                is_auto=is_auto,
                utm=user.utm if hasattr(user, "utm") else None,
                discount_percent=discount_percent,
                device_count=(int(data.get("device_count", 1)) if isinstance(data.get("device_count"), (int, str)) else None),
            )
        except Exception as e:
            logger.error(
                f"Ошибка при отправке уведомления о платеже: {e}",
                extra={'payment_id': payment.id}
            )

        return {"status": "ok"}
    except Exception as e:
        logger.error(
            f"Непредвиденная ошибка в webhook'е YooKassa: {e}",
            extra={'payment_id': payment.id if payment else 'unknown'}
        )
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/{tariff_id}")
async def pay(tariff_id: int, email: str, device_count: int = 1, user: Users = Depends(validate)):
    tariff = await Tariffs.get_or_none(id=tariff_id)
    if tariff is None:
        raise HTTPException(status_code=404, detail="Tariff not found")

    # Проверяем количество устройств
    if device_count < 1:
        device_count = 1
    
    months = int(tariff.months)
    # Рассчитываем цену для указанного количества устройств
    base_full_price = int(tariff.calculate_price(device_count))
    discounted_price, discount_id, discount_percent = await apply_personal_discount(user.id, base_full_price, months)
    full_price = float(discounted_price)
    user_balance = float(user.balance)

    try:
        current_date = date.today()
        target_date = add_months_safe(current_date, months)
        days = (target_date - current_date).days
    except Exception as e:
        logger.error(
            f"Ошибка при расчете дней подписки для пользователя {user.id} и тарифа {tariff_id}: {e}",
            extra={'user_id': user.id, 'tariff_id': tariff_id, 'months': months}
        )
        raise HTTPException(status_code=500, detail="Error calculating subscription days")

    # Проверка полной оплаты с баланса
    if user_balance >= full_price:
        logger.info(
            f"Оплата тарифа {tariff_id} для пользователя {user.id} полностью с баланса. "
            f"Цена: {full_price}, Баланс: {user_balance}, Скидка: {discount_percent}% (id={discount_id})"
        )

        current_date = date.today()
        additional_days = 0
        user_expired_at = normalize_date(user.expired_at)
        # --- NEW: перерасчёт остатка по старому тарифу ---
        if user_expired_at and user_expired_at > current_date and user.active_tariff_id:
            try:
                active_tariff = await ActiveTariffs.get(id=user.active_tariff_id)
                days_remaining = (user_expired_at - current_date).days
                logger.info(f"У пользователя {user.id} осталось {days_remaining} дней подписки")
                old_months = int(active_tariff.months)
                old_target_date = add_months_safe(current_date, old_months)
                old_total_days = (old_target_date - current_date).days
                unused_percent = days_remaining / old_total_days if old_total_days > 0 else 0
                unused_value = unused_percent * active_tariff.price
                logger.info(
                    f"Неиспользованная часть подписки пользователя {user.id}: "
                    f"{days_remaining}/{old_total_days} дней (стоимость: {unused_value:.2f} руб.)"
                )
                if full_price > 0:
                    # ИСПРАВЛЕННАЯ ЛОГИКА: рассчитываем через пропорцию от общей суммы
                    # Общая сумма = заплачено пользователем (full_price) + компенсация за старый тариф
                    total_amount = full_price + unused_value
                    
                    # Рассчитываем новый период подписки (стандартный для тарифа)
                    tariff_months = int(tariff.months)
                    new_target_date = add_months_safe(current_date, tariff_months)
                    new_total_days = (new_target_date - current_date).days
                    
                    # Пропорция: x дней / общая_сумма = полный_период_тарифа / цена_тарифа
                    # x = общая_сумма * полный_период_тарифа / цена_тарифа
                    calculated_days = int(total_amount * new_total_days / full_price)
                    
                    logger.info(
                        f"ИСПРАВЛЕННЫЙ расчёт (баланс) для пользователя {user.id}: "
                        f"Заплачено: {full_price:.2f} руб + Компенсация: {unused_value:.2f} руб = "
                        f"Общая сумма: {total_amount:.2f} руб. "
                        f"Пропорция: {calculated_days} дней = {total_amount:.2f} * {new_total_days} / {full_price:.2f}"
                    )
                    
                    # Устанавливаем рассчитанные дни как итоговые
                    additional_days = 0  # Сбрасываем, так как используем calculated_days
                    days = calculated_days  # Переопределяем days
            except Exception as e:
                logger.error(f"Ошибка при расчете переноса подписки для {user.id}: {str(e)}")
                additional_days = 0
        # При смене тарифа days уже рассчитано через пропорцию
        if 'calculated_days' not in locals():
            # Обычная покупка без смены тарифа - days уже рассчитано выше
            logger.info(f"Стандартное количество дней подписки: {days}")
        else:
            # days уже рассчитано через пропорцию при смене тарифа
            logger.info(f"Итоговое количество дней подписки (через пропорцию): {days}")

        user.balance -= full_price
        
        # При смене тарифа компенсация уже учтена в calculated_days
        # Поэтому устанавливаем дату от текущего дня, чтобы избежать двойного учёта
        if 'calculated_days' in locals():
            # Смена тарифа с компенсацией - устанавливаем от текущей даты
            user.expired_at = current_date + timedelta(days=days)
            logger.info(
                f"Смена тарифа (баланс) для пользователя {user.id}: установлена дата {user.expired_at} "
                f"({days} дней от текущей даты, рассчитано через пропорцию)"
            )
        else:
            # Обычное продление без смены тарифа
            await user.extend_subscription(days)
            logger.info(
                f"Продление (баланс) для пользователя {user.id}: дата {user.expired_at} "
                f"(с учетом оставшихся дней предыдущей подписки/триала)"
            )

        # Если у пользователя был пробный период, сбрасываем флаг
        if user.is_trial:
            user.is_trial = False
            logger.info(f"Сброшен флаг пробного периода для пользователя {user.id} после оплаты с баланса")

        # --- NEW: Создаём/обновляем ActiveTariffs и лимит устройств ---
        # Удаляем предыдущий активный тариф, если есть
        if user.active_tariff_id:
            old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
            if old_active_tariff:
                logger.info(f"Удаляем предыдущий активный тариф {user.active_tariff_id} пользователя {user.id}")
                await old_active_tariff.delete()
            else:
                logger.warning(f"Не найден активный тариф {user.active_tariff_id} для удаления у пользователя {user.id}")

        # код по сбросу HWID временно отключен
        # if user.remnawave_uuid:
            # await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)

        # Создаём новый активный тариф
        # ВАЖНО: сохраняем в price базовую стоимость тарифа без персональной скидки,
        # чтобы автоплатежи не применяли скидку дважды
        base_calculated_price = tariff.calculate_price(device_count)
        active_tariff = await ActiveTariffs.create(
            user=user,
            name=tariff.name,
            months=tariff.months,
            price=base_calculated_price,  # Цена без персональной скидки
            hwid_limit=device_count,  # Используем выбранное количество устройств
            progressive_multiplier=tariff.progressive_multiplier,
            residual_day_fraction=0.0
        )
        user.active_tariff_id = active_tariff.id

        # Устанавливаем hwid_limit пользователю из выбранного количества устройств
        user.hwid_limit = device_count
        logger.info(f"При покупке с баланса установлен hwid_limit={device_count} для пользователя {user.id}")

        # ВАЖНО: сохраняем active_tariff_id и hwid_limit в БД как можно раньше
        # чтобы минимизировать race condition с remnawave_updater
        try:
            await user.save(update_fields=["active_tariff_id", "hwid_limit"])
            logger.debug(f"Ранее сохранены active_tariff_id={active_tariff.id} и hwid_limit={device_count} для пользователя {user.id}")
        except Exception as persist_exc:
            logger.warning(f"Не удалось рано сохранить active_tariff_id/hwid_limit для {user.id}: {persist_exc}")

        # Сохраняем ВСЕ изменения пользователя (баланс, дата, is_trial и т.д.)
        await user.save()

        # После оплаты с баланса также обнуляем счётчик уменьшений
        if user.active_tariff_id:
            await ActiveTariffs.filter(id=user.active_tariff_id).update(devices_decrease_count=0)

        # Синхронизируем лимит устройств и дату окончания с RemnaWave
        if user.remnawave_uuid:
            remnawave_client = None
            try:
                remnawave_client = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value()
                )
                await remnawave_client.users.update_user(
                    uuid=user.remnawave_uuid,
                    expireAt=user.expired_at,
                    hwidDeviceLimit=device_count
                )
                logger.info(f"Синхронизирован hwid_limit={device_count} и expireAt={user.expired_at} для пользователя {user.id} в RemnaWave")
            except Exception as e:
                logger.error(f"Ошибка при синхронизации hwid_limit/expireAt с RemnaWave для пользователя {user.id}: {e}")
            finally:
                if remnawave_client:
                    try:
                        await remnawave_client.close()
                    except Exception as close_exc:
                        logger.warning(f"Ошибка при закрытии клиента RemnaWave: {close_exc}")

        payment_id = f"balance_{user.id}_{int(datetime.now().timestamp())}_{randint(100, 999)}"

        await ProcessedPayments.create(
            payment_id=payment_id,
            user_id=user.id,
            amount=full_price, # Итоговая стоимость (с учетом скидки)
            amount_external=0,
            amount_from_balance=full_price,
            status="succeeded" # Статус как при обычной успешной оплате
        )

        # Списываем одно использование скидки (если не постоянная)
        await consume_discount_if_needed(discount_id)

        referrer = await user.referrer() # Получаем реферера для уведомления админу
        try:
            await on_payment(
                user_id=user.id,
                is_sub=user.is_subscribed, # Передаем текущий статус автопродления
                referrer=referrer.name() if referrer else None,
                amount=full_price, # Сумма уведомления - итоговая цена с учетом скидки
                months=months,
                method="balance", # Указываем метод оплаты
                payment_id=payment_id,
                utm=user.utm if hasattr(user, "utm") else None,
                discount_percent=discount_percent,
                device_count=device_count,
            )
        except Exception as e:
            logger.error(
                f"Ошибка при отправке уведомления о платеже с баланса: {e}",
                extra={'payment_id': payment_id, 'user_id': user.id}
            )
            
        return {"status": "success", "message": "Оплачено с бонусного баланса"}

    else:
        # Логика частичной оплаты
        amount_to_pay = max(1.0, full_price - user_balance) # Минимум 1 рубль для Yookassa
        amount_from_balance = full_price - amount_to_pay

        logger.info(
            f"Создание платежа для пользователя {user.id}. "
            f"Тариф: {tariff_id}, Полная цена: {full_price}, Баланс: {user_balance}, "
            f"К оплате: {amount_to_pay}, С баланса: {amount_from_balance}"
        )

        metadata = {
            "user_id": user.id,
            "month": months,
            "amount_from_balance": amount_from_balance, # Добавляем сумму списания с баланса
            "tariff_id": tariff.id,
            "device_count": device_count,  # Добавляем количество устройств
            "base_full_price": base_full_price,
            "discounted_price": discounted_price,
            "discount_percent": discount_percent,
            "discount_id": discount_id,
        }

        # Обернуть синхронный вызов YooKassa в async с таймаутом
        try:
            payment_data = {
                "amount": {
                    "value": str(amount_to_pay),
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"https://t.me/{await get_bot_username()}/"
                },
                "metadata": metadata,
                "capture": True,
                "description": f"Оплата подписки пользователя {user.id} (Тариф: {tariff.name})",
                "save_payment_method": True,
                "receipt": {
                    "customer": {"email": email},
                    "items": [{
                        "description": f"Подписка пользователя {user.id} ({tariff.name})",
                        "quantity": "1",
                        "amount": {
                            "value": str(amount_to_pay),
                            "currency": "RUB"
                        },
                        "vat_code": 1, # TODO: Проверить НДС
                        "payment_subject": "service",
                        "payment_mode": "full_payment"
                    }]
                }
            }

            idempotence_key = str(randint(100000, 999999999999))

            # Используем asyncio.to_thread для неблокирующего вызова с таймаутом
            payment = await asyncio.wait_for(
                asyncio.to_thread(partial(Payment.create, payment_data, idempotence_key)),
                timeout=30.0
            )

        except asyncio.TimeoutError:
            logger.error(
                f"Таймаут при создании платежа YooKassa для пользователя {user.id}. "
                f"Тариф: {tariff_id}, Сумма: {amount_to_pay}",
                extra={'user_id': user.id, 'tariff_id': tariff_id, 'amount': amount_to_pay}
            )
            raise HTTPException(
                status_code=503,
                detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже."
            )
        except (ConnectTimeoutError, ReadTimeoutError, RequestsConnectionError, RequestsTimeout) as network_err:
            logger.error(
                f"Сетевая ошибка при создании платежа YooKassa для пользователя {user.id}: {network_err}",
                extra={'user_id': user.id, 'tariff_id': tariff_id, 'amount': amount_to_pay, 'error_type': type(network_err).__name__}
            )
            raise HTTPException(
                status_code=503,
                detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже."
            )
        except Exception as e:
            logger.error(
                f"Неожиданная ошибка при создании платежа YooKassa для пользователя {user.id}: {e}",
                extra={'user_id': user.id, 'tariff_id': tariff_id, 'amount': amount_to_pay, 'error_type': type(e).__name__},
                exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail="Ошибка при создании платежа. Пожалуйста, попробуйте позже."
            )

        # Резервации отключены

        return {"redirect_to": payment.confirmation.confirmation_url}

async def create_auto_payment(user: Users, disable_on_fail: bool = True) -> bool:
    """
    Создает автоматический платеж для продления подписки
    Returns:
        bool: True если платеж успешно создан, False в случае ошибки
    """
    # Вычисляем will_retry один раз для всех уведомлений об ошибках
    user_expired_at = normalize_date(user.expired_at)
    will_retry = user_expired_at is not None and (user_expired_at - date.today()).days >= 0

    try:
        # --- Modify auto-payment logic to use active_tariff_id ---
        if not user.active_tariff_id:
            logger.error(f"У пользователя {user.id} не установлен active_tariff_id. Автопродление невозможно.")
            await notify_auto_renewal_failure(user, reason="Отсутствует информация о последнем активном тарифе", will_retry=will_retry)
            # Отключаем подписку, если нет активного тарифа
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Уведомляем админа об отключении автопродления из-за отсутствия тарифа
            await cancel_subscription(user, reason="Отсутствует информация о последнем активном тарифе")
            return False

        # Получаем детали тарифа из ActiveTariffs
        active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if not active_tariff:
            logger.error(
                f"Не найден активный тариф с ID {user.active_tariff_id} для пользователя {user.id}",
                extra={'user_id': user.id, 'active_tariff_id': user.active_tariff_id}
            )
            # Отключаем автопродление, если активный тариф не найден
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            logger.warning(f"Автопродление отключено для {user.id} из-за отсутствия активного тарифа ID={user.active_tariff_id} в базе.")
            await notify_auto_renewal_failure(user, reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) для автопродления", will_retry=will_retry)
            # Уведомляем админа об отключении автопродления из-за отсутствия тарифа в базе
            await cancel_subscription(user, reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) в базе")
            return False

        logger.info(f"Автопродление для пользователя {user.id}. Используется активный тариф ID={active_tariff.id} (Name: {active_tariff.name}, Price: {active_tariff.price})")

        months = int(active_tariff.months)
        base_full_price = int(active_tariff.price)
        # Применяем персональную скидку (если есть)
        discounted_price, discount_id, discount_percent = await apply_personal_discount(user.id, base_full_price, months)
        full_price = float(discounted_price)
        user_balance = float(user.balance)

        try:
            current_date = date.today()
            target_date = add_months_safe(current_date, months)
            days = (target_date - current_date).days
        except Exception as e:
            logger.error(
                f"Ошибка при расчете дней подписки для автоплатежа {user.id}, тариф {active_tariff.id}: {e}",
                extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'months': months}
            )
            # Уведомляем пользователя о неудаче (здесь маловероятно, но все же)
            await notify_auto_renewal_failure(user, reason="Ошибка при расчете срока продления", will_retry=will_retry)
            return False

        # Проверка полной оплаты с баланса
        if user_balance >= full_price:
            logger.info(
                f"Автопродление тарифа {active_tariff.id} для пользователя {user.id} полностью с баланса. "
                f"Цена: {full_price}, Баланс: {user_balance}"
            )

            initial_balance = user.balance
            user.balance -= full_price
            await user.extend_subscription(days)
            # Сбрасываем триал, если был (маловероятно для автоплатежа, но на всякий случай)
            if user.is_trial:
                user.is_trial = False
                logger.info(f"Сброшен флаг пробного периода для {user.id} при автооплате с баланса")
            await user.save()

            payment_id = f"balance_auto_{user.id}_{int(datetime.now().timestamp())}_{randint(100, 999)}"

            await ProcessedPayments.create(
                payment_id=payment_id,
                user_id=user.id,
                amount=full_price, # Итоговая стоимость с учетом скидки
                amount_external=0,
                amount_from_balance=full_price,
                status="succeeded" # Статус как при обычной успешной оплате
            )
            
            logger.info(
                 f"Автоплатеж для пользователя {user.id} успешно выполнен с баланса. "
                 f"Списано: {full_price}. Баланс до: {initial_balance}, После: {user.balance}, Скидка: {discount_percent}% (id={discount_id})"
            )

            # Уведомления (админу)
            referrer = await user.referrer()
            try:
                await on_payment(
                    user_id=user.id,
                    is_sub=user.is_subscribed,
                    referrer=referrer.name() if referrer else None,
                    amount=full_price,
                    months=months,
                    method="balance_auto", # Указываем метод
                    payment_id=payment_id,
                    is_auto=True,
                    utm=user.utm if hasattr(user, "utm") else None,
                    discount_percent=discount_percent,
                    device_count=active_tariff.hwid_limit if hasattr(active_tariff, "hwid_limit") else None,
                )
            except Exception as e:
                logger.error(
                    f"Ошибка при отправке уведомления об автоплатеже с баланса: {e}",
                    extra={'payment_id': payment_id, 'user_id': user.id}
                )
            
            # Списываем использование скидки (если не постоянная)
            await consume_discount_if_needed(discount_id)

            # Уведомляем пользователя об успешном автопродлении с баланса
            await notify_auto_renewal_success_balance(user, days=days, amount=full_price)
            # Сообщение пользователю о начислении круток
            try:
                await notify_spin_awarded(user=user, added_attempts=int(months), total_attempts=int(user.prize_wheel_attempts or 0))
            except Exception as e_notify_spins:
                logger.error(f"Ошибка уведомления о крутках (баланс) для {user.id}: {e_notify_spins}")

            # Начисление круток за автосписание с баланса: 1 крутка за каждый месяц
            try:
                attempts_before = int(getattr(user, "prize_wheel_attempts", 0) or 0)
                if months and months > 0:
                    user.prize_wheel_attempts = attempts_before + int(months)
                    await user.save()
                    logger.info(
                        f"Начислено {months} круток за автосписание (баланс) пользователю {user.id}. Было: {attempts_before}, стало: {user.prize_wheel_attempts}"
                    )
            except Exception as award_exc:
                logger.error(f"Не удалось начислить крутки за автосписание (баланс) для {user.id}: {award_exc}")
            
            return True # Автоплатеж успешен

        else:
            # Логика частичной оплаты
            amount_to_pay = max(1.0, full_price - user_balance)
            amount_from_balance = full_price - amount_to_pay

            logger.info(
                f"Создание автоплатежа Yookassa для пользователя {user.id}. "
                f"Тариф: {active_tariff.id}, Полная цена: {full_price}, Баланс: {user_balance}, "
                f"К оплате: {amount_to_pay}, С баланса: {amount_from_balance}"
            )

            metadata = {
                "user_id": user.id,
                "month": months,
                "is_auto": True,
                "amount_from_balance": amount_from_balance,
                "disable_on_fail": disable_on_fail,
                "base_full_price": base_full_price,
                "discounted_price": discounted_price,
                "discount_percent": discount_percent,
                "discount_id": discount_id,
            }

            # Создаем автоплатеж Yookassa с таймаутом
            try:
                payment_data = {
                    "amount": {
                        "value": str(amount_to_pay),
                        "currency": "RUB"
                    },
                    "payment_method_id": user.renew_id,
                    "metadata": metadata,
                    "capture": True,
                    "description": f"Автопродление подписки пользователя {user.id} ({active_tariff.name})",
                    "receipt": {
                        "customer": {"email": user.email if user.email else "auto@bloopcat.ru"},
                        "items": [{
                            "description": f"Автопродление подписки пользователя {user.id} ({active_tariff.name})",
                            "quantity": "1",
                            "amount": {
                                "value": str(amount_to_pay),
                                "currency": "RUB"
                            },
                            "vat_code": 1, # TODO: Проверить НДС
                            "payment_subject": "service",
                            "payment_mode": "full_payment"
                        }]
                    }
                }

                idempotence_key = str(randint(100000, 999999999999))

                # Используем asyncio.to_thread для неблокирующего вызова с таймаутом
                payment = await asyncio.wait_for(
                    asyncio.to_thread(partial(Payment.create, payment_data, idempotence_key)),
                    timeout=30.0
                )

            except asyncio.TimeoutError:
                logger.error(
                    f"Таймаут при создании автоплатежа YooKassa для пользователя {user.id}. "
                    f"Тариф: {active_tariff.id}, Сумма: {amount_to_pay}",
                    extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'amount': amount_to_pay}
                )
                await notify_auto_renewal_failure(
                    user,
                    reason="Сервис оплаты временно недоступен (таймаут)",
                    will_retry=will_retry
                )
                return False
            except (ConnectTimeoutError, ReadTimeoutError, RequestsConnectionError, RequestsTimeout) as network_err:
                logger.error(
                    f"Сетевая ошибка при создании автоплатежа YooKassa для пользователя {user.id}: {network_err}",
                    extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'amount': amount_to_pay, 'error_type': type(network_err).__name__}
                )
                await notify_auto_renewal_failure(
                    user,
                    reason="Сервис оплаты временно недоступен (ошибка сети)",
                    will_retry=will_retry
                )
                return False
            except Exception as create_exc:
                logger.error(
                    f"Неожиданная ошибка при создании автоплатежа YooKassa для пользователя {user.id}: {create_exc}",
                    extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'amount': amount_to_pay, 'error_type': type(create_exc).__name__},
                    exc_info=True
                )
                # Для непредвиденных ошибок пробрасываем дальше
                raise

            # Сбрасываем триал, если пользователь платит первый раз (даже автоплатежом)
            if user.is_trial:
                user.is_trial = False
                await user.save()
                logger.info(
                    f"Сброшен флаг пробного периода для {user.id} при создании автоплатежа Yookassa",
                    extra={
                        'payment_id': payment.id,
                        'user_id': user.id
                    }
                )

            logger.info(
                f"Создан автоплатеж Yookassa для пользователя {user.id}",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': payment.status
                }
            )
            return True # Автоплатеж создан (результат будет в вебхуке)

    except Exception as e:
        logger.error(
            f"Ошибка при создании автоплатежа для пользователя {user.id}: {e}",
            extra={'user_id': user.id}
        )
        # Отключаем автопродление только если это последняя попытка
        if disable_on_fail:
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Уведомляем админа об отключении автопродления из-за ошибки
            await cancel_subscription(user, reason=f"Ошибка при создании автоплатежа: {str(e)}")
        logger.warning(f"Автопродление отключено для {user.id} из-за ошибки при создании автоплатежа: {e}")
        await notify_auto_renewal_failure(user, reason=f"Внутренняя ошибка сервера при попытке автопродления", will_retry=will_retry)
        return False

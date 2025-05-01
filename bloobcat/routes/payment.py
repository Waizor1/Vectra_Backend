from random import randint
from datetime import date, datetime, timedelta
import json
import asyncio
import random

from fastapi import APIRouter, Depends, HTTPException, Header, Request # type: ignore
from yookassa import Configuration, Payment, Webhook
from yookassa.domain.notification import WebhookNotification, WebhookNotificationEventType

from bloobcat.bot.bot import get_bot_username
from bloobcat.bot.notifications.admin import on_payment, cancel_subscription
from bloobcat.bot.notifications.general.referral import on_referral_payment
from bloobcat.bot.notifications.subscription.renewal import (
    notify_auto_renewal_success_balance,
    notify_auto_renewal_failure,
    notify_renewal_success_yookassa
)
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.settings import yookassa_settings, remnawave_settings
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_payment_logger
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.hwid_utils import cleanup_user_hwid_devices

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
            
            # Сохраняем информацию о возврате
            await ProcessedPayments.create(
                payment_id=payment.id,
                user_id=user.id,
                amount=float(payment.amount.value),
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
                status="canceled"
            )
            
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
                await notify_auto_renewal_failure(user, reason="Платеж был отменен", will_retry=(user.expired_at and (user.expired_at - date.today()).days >= 0))
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
                await notify_auto_renewal_failure(user, reason=f"Платеж не прошел (статус: {payment.status})", will_retry=(user.expired_at and (user.expired_at - date.today()).days >= 0))
            return {"status": "ok"}
        
        try:
            months = int(data["month"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректное значение месяцев в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id}
            )
            return {"status": "error", "message": "Invalid month value"}
        
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
            
            if user.expired_at and user.expired_at > current_date and user.active_tariff_id:
                # У пользователя есть действующая подписка
                try:
                    # Получаем активный тариф
                    active_tariff = await ActiveTariffs.get(id=user.active_tariff_id)
                    
                    # Вычисляем оставшиеся дни подписки
                    days_remaining = (user.expired_at - current_date).days
                    logger.info(f"У пользователя {user.id} осталось {days_remaining} дней подписки")
                    
                    # Рассчитываем количество дней, которое давал старый тариф
                    old_months = active_tariff.months
                    old_target_date = current_date.replace(
                        year=current_date.year + ((current_date.month + old_months - 1) // 12),
                        month=((current_date.month + old_months - 1) % 12) + 1
                    )
                    old_total_days = (old_target_date - current_date).days
                    
                    # Рассчитываем процент неиспользованной подписки
                    unused_percent = days_remaining / old_total_days if old_total_days > 0 else 0
                    unused_value = unused_percent * active_tariff.price
                    
                    logger.info(
                        f"Неиспользованная часть подписки пользователя {user.id}: " 
                        f"{days_remaining}/{old_total_days} дней ({unused_percent:.2%}), " 
                        f"стоимость: {unused_value:.2f} руб."
                    )
                    
                    # Рассчитываем, сколько дней даст неиспользованная сумма в новом тарифе
                    if new_tariff.price > 0:
                        # Рассчитываем новый период подписки
                        new_target_date = current_date.replace(
                            year=current_date.year + ((current_date.month + new_tariff.months - 1) // 12),
                            month=((current_date.month + new_tariff.months - 1) % 12) + 1
                        )
                        new_total_days = (new_target_date - current_date).days
                        
                        # Сколько дней даст неиспользованная сумма в новом тарифе
                        additional_days = int(unused_value / new_tariff.price * new_total_days)
                        logger.info(
                            f"Перенос времени для пользователя {user.id}: "
                            f"{unused_value:.2f} руб. = {additional_days} дней в новом тарифе"
                        )
                except Exception as e:
                    logger.error(f"Ошибка при расчете переноса подписки для {user.id}: {str(e)}")
                    additional_days = 0  # При ошибке не добавляем дополнительные дни
            
            # Рассчитываем точное количество дней для указанного количества месяцев + дополнительные дни
            current_date = date.today()
            target_date = current_date.replace(
                year=current_date.year + ((current_date.month + months - 1) // 12),
                month=((current_date.month + months - 1) % 12) + 1
            )
            days = (target_date - current_date).days + additional_days
            logger.info(f"Итоговое количество дней подписки: {days} ({(target_date - current_date).days} + {additional_days})")
        else:
            # Если нет tariff_id, значит это автоплатеж или другой тип платежа, просто рассчитываем дни как обычно
            current_date = date.today()
            target_date = current_date.replace(
                year=current_date.year + ((current_date.month + months - 1) // 12),
                month=((current_date.month + months - 1) % 12) + 1
            )
            days = (target_date - current_date).days
            logger.info(f"Стандартное количество дней подписки: {days}")
        
        # Рассчитываем точное количество дней для указанного количества месяцев
        try:
            amount_from_balance = float(data.get("amount_from_balance", 0))
            if amount_from_balance > 0:
                initial_balance = user.balance
                user.balance = max(0, user.balance - amount_from_balance)
                logger.info(
                    f"Списание с реферального баланса пользователя {user.id}. "
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
                # Если это новый тариф (tariff_id присутствует), устанавливаем новую дату
                # иначе расширяем существующую подписку
                if tariff_id is not None:
                    # Устанавливаем новую дату окончания
                    user.expired_at = current_date + timedelta(days=days)
                    logger.info(
                        f"Установлена новая дата истечения для пользователя {user.id}: {user.expired_at} "
                        f"(сброшена предыдущая дата и установлено {days} дней)"
                    )
                else:
                    # Расширяем существующую подписку при обычном продлении
                    await user.extend_subscription(days)
                
            # If a tariff_id is provided in metadata, ensure it's created in ActiveTariffs and assign to user
            if tariff_id is not None:
                original = await Tariffs.get_or_none(id=tariff_id)
                if original:
                    # Удаляем предыдущий активный тариф, если он есть
                    if user.active_tariff_id:
                        old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
                        if old_active_tariff:
                            logger.info(f"Удаляем предыдущий активный тариф {user.active_tariff_id} пользователя {user.id}")
                            await old_active_tariff.delete()
                        else:
                            logger.warning(f"Не найден активный тариф {user.active_tariff_id} для удаления у пользователя {user.id}")
                    
                    # При смене тарифа удаляем все HWID устройства пользователя в RemnaWave
                    if user.remnawave_uuid:
                        await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)
                    
                    # Create a new active tariff entry with random ID
                    active_tariff = await ActiveTariffs.create(
                        user=user,  # Link to this user
                        name=original.name,
                        months=original.months,
                        price=original.price,
                        hwid_limit=original.hwid_limit
                    )
                    # Link user to this active tariff
                    user.active_tariff_id = active_tariff.id
                    logger.info(f"Created ActiveTariff {active_tariff.id} for user {user.id} based on tariff {original.id}")
                else:
                    logger.error(f"Original tariff {tariff_id} not found; skipping ActiveTariffs")
            
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
                        hwid_limit = original.hwid_limit
                        logger.info(f"Новая подписка: устанавливаем hwid_limit={hwid_limit} из тарифа ID={original.id}")
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
                            
                            await remnawave_client.users.update_user(
                                uuid=user.remnawave_uuid,
                                **update_params
                            )
                            
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
                    'is_auto': is_auto
                }
            )
            
            # Уведомляем пользователя об успешном продлении, ТОЛЬКО ЕСЛИ это был автоплатеж
            if is_auto:
                try:
                    await notify_renewal_success_yookassa(
                        user=user,
                        days=days,
                        amount_paid_via_yookassa=amount_paid_via_yookassa,
                        amount_from_balance=amount_from_balance
                    )
                except Exception as notify_exc:
                     logger.error(f"Ошибка при отправке уведомления об успешном АВТОпродлении для {user.id}: {notify_exc}")
            
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
async def pay(tariff_id: int, email: str, user: Users = Depends(validate)):
    tariff = await Tariffs.get_or_none(id=tariff_id)
    if tariff is None:
        raise HTTPException(status_code=404, detail="Tariff not found")

    full_price = float(tariff.price)
    user_balance = float(user.balance)
    months = int(tariff.months)

    try:
        current_date = date.today()
        target_date = current_date.replace(year=current_date.year + ((current_date.month + months - 1) // 12),
                                     month=((current_date.month + months - 1) % 12) + 1)
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
            f"Цена: {full_price}, Баланс: {user_balance}"
        )

        user.balance -= full_price
        await user.extend_subscription(days)
        await user.save()

        payment_id = f"balance_{user.id}_{int(datetime.now().timestamp())}_{randint(100, 999)}"

        await ProcessedPayments.create(
            payment_id=payment_id,
            user_id=user.id,
            amount=full_price, # Полная стоимость тарифа
            status="succeeded" # Статус как при обычной успешной оплате
        )

        referrer = await user.referrer() # Получаем реферера для уведомления админу
        try:
            await on_payment(
                user_id=user.id,
                is_sub=user.is_subscribed, # Передаем текущий статус автопродления
                referrer=referrer.name() if referrer else None,
                amount=full_price, # Сумма уведомления - полная цена тарифа
                months=months,
                method="balance", # Указываем метод оплаты
                payment_id=payment_id,
            )
        except Exception as e:
            logger.error(
                f"Ошибка при отправке уведомления о платеже с баланса: {e}",
                extra={'payment_id': payment_id, 'user_id': user.id}
            )
            
        return {"status": "success", "message": "Оплачено с реферального баланса"}

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
            "tariff_id": tariff.id
        }

        payment = Payment.create({
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
        }, str(randint(100000, 999999999999)))

        return {"redirect_to": payment.confirmation.confirmation_url}

async def create_auto_payment(user: Users, disable_on_fail: bool = True) -> bool:
    """
    Создает автоматический платеж для продления подписки
    Returns:
        bool: True если платеж успешно создан, False в случае ошибки
    """
    try:
        # --- Modify auto-payment logic to use active_tariff_id ---
        if not user.active_tariff_id:
            logger.error(f"У пользователя {user.id} не установлен active_tariff_id. Автопродление невозможно.")
            await notify_auto_renewal_failure(user, reason="Отсутствует информация о последнем активном тарифе", will_retry=(user.expired_at and (user.expired_at - date.today()).days >= 0))
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
            await notify_auto_renewal_failure(user, reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) для автопродления", will_retry=(user.expired_at and (user.expired_at - date.today()).days >= 0))
            # Уведомляем админа об отключении автопродления из-за отсутствия тарифа в базе
            await cancel_subscription(user, reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) в базе")
            return False

        logger.info(f"Автопродление для пользователя {user.id}. Используется активный тариф ID={active_tariff.id} (Name: {active_tariff.name}, Price: {active_tariff.price})")

        full_price = float(active_tariff.price)
        user_balance = float(user.balance)
        months = int(active_tariff.months)

        try:
            current_date = date.today()
            target_date = current_date.replace(year=current_date.year + ((current_date.month + months - 1) // 12),
                                         month=((current_date.month + months - 1) % 12) + 1)
            days = (target_date - current_date).days
        except Exception as e:
            logger.error(
                f"Ошибка при расчете дней подписки для автоплатежа {user.id}, тариф {active_tariff.id}: {e}",
                extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'months': months}
            )
            # Уведомляем пользователя о неудаче (здесь маловероятно, но все же)
            await notify_auto_renewal_failure(user, reason="Ошибка при расчете срока продления", will_retry=(user.expired_at and (user.expired_at - date.today()).days >= 0))
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
                amount=full_price, # Полная стоимость тарифа
                status="succeeded" # Статус как при обычной успешной оплате
            )
            
            logger.info(
                 f"Автоплатеж для пользователя {user.id} успешно выполнен с баланса. "
                 f"Списано: {full_price}. Баланс до: {initial_balance}, После: {user.balance}"
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
                )
            except Exception as e:
                logger.error(
                    f"Ошибка при отправке уведомления об автоплатеже с баланса: {e}",
                    extra={'payment_id': payment_id, 'user_id': user.id}
                )
            
            # Уведомляем пользователя об успешном автопродлении с баланса
            await notify_auto_renewal_success_balance(user, days=days, amount=full_price)
            
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
            }

            # Создаем автоплатеж Yookassa
            payment = Payment.create({
                "amount": {
                    "value": str(amount_to_pay),
                    "currency": "RUB"
                },
                "payment_method_id": user.renew_id,
                "metadata": metadata,
                "capture": True,
                "description": f"Автопродление подписки пользователя {user.id} ({active_tariff.name})",
                "receipt": {
                    "customer": {"email": user.email if user.email else "auto@bloobcat.ru"},
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
            }, str(randint(100000, 999999999999)))

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
        await notify_auto_renewal_failure(user, reason=f"Внутренняя ошибка сервера при попытке автопродления", will_retry=(user.expired_at and (user.expired_at - date.today()).days >= 0))
        return False

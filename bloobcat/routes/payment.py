from random import randint
from datetime import date, datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Header, Request # type: ignore
from yookassa import Configuration, Payment, Webhook
from yookassa.domain.notification import WebhookNotification, WebhookNotificationEventType

from bloobcat.bot.bot import get_bot_username
from bloobcat.bot.notifications.admin import on_payment
from bloobcat.bot.notifications.user import (
    on_referral_payment, 
    notify_auto_renewal_success_balance, 
    notify_auto_renewal_failure,
    notify_renewal_success_yookassa
)
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.settings import yookassa_settings
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_payment_logger

# Инициализируем клиент ЮKассы
Configuration.account_id = yookassa_settings.shop_id
Configuration.secret_key = yookassa_settings.secret_key.get_secret_value()

router = APIRouter(prefix="/pay", tags=["pay"])
logger = get_payment_logger()

@router.get("/tariffs")
async def get_tariffs():
    return await Tariffs().all()

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
            # При отмене платежа также отключаем автопродление
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            
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
            # --- Добавляем уведомление пользователю, если это был автоплатеж ---
            if data.get("is_auto", False):
                await notify_auto_renewal_failure(user, reason="Платеж был отменен")
            # --- Конец добавления ---
            return {"status": "ok"}
        
        if event != WebhookNotificationEventType.PAYMENT_SUCCEEDED:
            return {"status": "ok"}
        
        if payment.status != "succeeded":
            # --- Добавляем обработку неуспешного статуса автоплатежа ---
            logger.warning(f"Автоплатеж {payment.id} для пользователя {user.id} завершился со статусом {payment.status}")
            if data.get("is_auto", False):
                # Отключаем автопродление
                user.is_subscribed = False
                user.renew_id = None
                await user.save()
                logger.info(f"Автопродление отключено для {user.id} из-за неуспешного статуса автоплатежа ({payment.status})")
                # Уведомляем пользователя
                await notify_auto_renewal_failure(user, reason=f"Платеж не прошел (статус: {payment.status})")
            # --- Конец добавления ---
            return {"status": "ok"}
        
        try:
            months = int(data["month"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректное значение месяцев в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id}
            )
            return {"status": "error", "message": "Invalid month value"}
        
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
            
            current_date = date.today()
            target_date = current_date.replace(year=current_date.year + ((current_date.month + months - 1) // 12),
                                         month=((current_date.month + months - 1) % 12) + 1)
            days = (target_date - current_date).days
            
            await user.extend_subscription(days)
            
            # Проверяем, является ли это автоплатежом
            is_auto = data.get("is_auto", False)
            
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
            
            await user.save() # Сохраняем пользователя (включая обновленный баланс)
            
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
                amount=amount,
                months=months,
                method="yookassa",
                referrer=referrer.name() if referrer else None,
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
        if user.is_trial:
            user.is_trial = False
            logger.info(f"Сброшен флаг пробного периода для {user.id} при оплате с баланса")
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
                amount=full_price, # Сумма уведомления - полная цена тарифа
                months=months,
                method="balance", # Указываем метод оплаты
                referrer=referrer.name() if referrer else None,
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
            "amount_from_balance": amount_from_balance # Добавляем сумму списания с баланса
        }

        payment = Payment.create({
            "amount": {
                "value": str(amount_to_pay),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"https://t.me/{await get_bot_username()}/cybertest" # Используем /cybertest
            },
            "metadata": metadata,
            "capture": True,
            "description": f"Оплата заказа Bloobcat (Тариф: {tariff.name})",
            "save_payment_method": True,
            "receipt": {
                "customer": {"email": email},
                "items": [{
                    "description": f"WEB-Сервис Bloobcat ({tariff.name})",
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

async def create_auto_payment(user: Users) -> bool:
    """
    Создает автоматический платеж для продления подписки
    Returns:
        bool: True если платеж успешно создан, False в случае ошибки
    """
    try:
        # Получаем последний платеж пользователя из истории платежей
        last_payment = await ProcessedPayments.filter(
            user_id=user.id,
            status="succeeded"
        ).order_by("-processed_at").first()

        if not last_payment:
            logger.error(
                f"Не найден последний УСПЕШНЫЙ платеж для пользователя {user.id} для автопродления",
                extra={'user_id': user.id}
            )
            # Уведомляем пользователя о неудаче
            await notify_auto_renewal_failure(user, reason="Не найден последний УСПЕШНЫЙ платеж для автопродления")
            return False

        # Получаем тариф на основе суммы последнего успешного платежа
        tariff = await Tariffs.get_or_none(price=last_payment.amount)
        if not tariff:
            logger.error(
                f"Не найден тариф для суммы {last_payment.amount} при создании автоплатежа для {user.id}",
                extra={'user_id': user.id, 'last_payment_amount': last_payment.amount}
            )
            # Отключаем автопродление, если тариф не найден (цена изменилась?)
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            logger.warning(f"Автопродление отключено для {user.id} из-за отсутствия тарифа на сумму {last_payment.amount}")
            # Уведомляем пользователя о неудаче
            await notify_auto_renewal_failure(user, reason=f"Не найден актуальный тариф для автопродления (сумма {last_payment.amount} руб.)")
            return False

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
                f"Ошибка при расчете дней подписки для автоплатежа {user.id}, тариф {tariff.id}: {e}",
                extra={'user_id': user.id, 'tariff_id': tariff.id, 'months': months}
            )
            # Уведомляем пользователя о неудаче (здесь маловероятно, но все же)
            await notify_auto_renewal_failure(user, reason="Ошибка при расчете срока продления")
            return False

        # Проверка полной оплаты с баланса
        if user_balance >= full_price:
            logger.info(
                f"Автопродление тарифа {tariff.id} для пользователя {user.id} полностью с баланса. "
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
                    amount=full_price,
                    months=months,
                    method="balance_auto", # Указываем метод
                    referrer=referrer.name() if referrer else None,
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
                f"Тариф: {tariff.id}, Полная цена: {full_price}, Баланс: {user_balance}, "
                f"К оплате: {amount_to_pay}, С баланса: {amount_from_balance}"
            )

            metadata = {
                "user_id": user.id,
                "month": months,
                "is_auto": True,
                "amount_from_balance": amount_from_balance
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
                "description": f"Автопродление подписки Bloobcat ({tariff.name})",
                "receipt": {
                    "customer": {"email": user.email if user.email else "auto@bloobcat.ru"},
                    "items": [{
                        "description": f"Автопродление WEB-Сервиса Bloobcat ({tariff.name})",
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
        # Отключаем автопродление при любой ошибке в этой функции
        user.is_subscribed = False
        user.renew_id = None
        await user.save()
        logger.warning(f"Автопродление отключено для {user.id} из-за ошибки при создании автоплатежа: {e}")
        # Уведомляем пользователя о неудаче
        await notify_auto_renewal_failure(user, reason=f"Внутренняя ошибка сервера при попытке автопродления")
        return False

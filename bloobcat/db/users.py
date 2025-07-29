from datetime import date, timedelta
from typing import Optional, Any

from aiogram.types import User
from aiogram.utils.web_app import WebAppUser
from fastapi import Request
from fastadmin import TortoiseModelAdmin, register
from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator

from bloobcat.bot.notifications.admin import on_activated_bot
from bloobcat.config import referral_percent

import logging

from bloobcat.settings import remnawave_settings, test_mode, app_settings
from bloobcat.logger import get_logger

from bloobcat.db.active_tariff import ActiveTariffs

import zlib

# Import for new trial granted notification
from bloobcat.bot.notifications.trial.granted import notify_trial_granted

logger = get_logger("users_db")


def crc32(a: str):
    return format(zlib.crc32(str(a).encode("utf-8")), "08x")


def get_family_url(user_id) -> str:
    return (
        crc32(f"{user_id}family") + crc32(f"family {user_id}") + "blubcat"
    )


class Users(models.Model):
    id = fields.BigIntField(primary_key=True)
    username = fields.CharField(max_length=100, null=True)
    full_name = fields.CharField(max_length=1000)
    expired_at = fields.DateField(null=True)
    is_registered = fields.BooleanField(default=False)
    balance = fields.IntField(default=0)
    referred_by = fields.BigIntField(default=0)
    is_admin = fields.BooleanField(default=False)
    is_partner = fields.BooleanField(default=False)
    custom_referral_percent = fields.IntField(default=0)
    registration_date = fields.DatetimeField(auto_now_add=True)
    referrals = fields.IntField(default=0)
    is_subscribed = fields.BooleanField(default=False)
    utm = fields.CharField(max_length=100, null=True)
    renew_id = fields.CharField(max_length=100, null=True)
    connected_at = fields.DatetimeField(null=True)
    email = fields.CharField(max_length=255, null=True)
    language_code = fields.CharField(max_length=5, default="ru", description="Код языка пользователя для локализации уведомлений")
    created_at = fields.DatetimeField(auto_now_add=True)
    is_trial = fields.BooleanField(default=False)
    used_trial = fields.BooleanField(default=False)
    notification_2h_sent = fields.BooleanField(default=False)
    notification_24h_sent = fields.BooleanField(default=False)
    remnawave_uuid = fields.UUIDField(null=True)  # UUID пользователя в RemnaWave
    last_hwid_reset = fields.DatetimeField(null=True, description="Дата и время последнего ручного сброса HWID устройств")
    familyurl = fields.CharField(max_length=100, null=True)
    active_tariff: fields.ForeignKeyNullableRelation["ActiveTariffs"] = fields.ForeignKeyField(
        "models.ActiveTariffs", related_name="users", null=True, on_delete=fields.SET_NULL, description="ID активного тарифа пользователя"
    )
    hwid_limit = fields.IntField(null=True, description="Личный лимит устройств (переопределяет тариф и настройки панели)")
    is_blocked = fields.BooleanField(default=False, description="Пользователь заблокировал бота")
    blocked_at = fields.DatetimeField(null=True, description="Дата и время блокировки")
    last_failed_message_at = fields.DatetimeField(null=True, description="Последняя неуспешная попытка отправки")
    failed_message_count = fields.IntField(default=0, description="Количество неуспешных попыток подряд")

    async def _ensure_remnawave_user(self) -> bool:
        """
        Создает пользователя в RemnaWave, если он еще не создан.
        Возвращает True, если пользователь был создан, False - если уже существовал.
        """
        if self.remnawave_uuid:
            # Пользователь уже создан в RemnaWave
            logger.info(f"Пользователь {self.id} уже имеет UUID в RemnaWave: {self.remnawave_uuid}")
            return False
        
        try:
            # Импортируем здесь, чтобы избежать циклических импортов
            from bloobcat.routes.remnawave.client import RemnaWaveClient
            from bloobcat.settings import remnawave_settings
            
            remnawave = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
            
            try:
                # Определяем дату истечения
                expire_at_date = self.expired_at
                if expire_at_date is None:
                    # Если даты нет и триал не использован, назначаем триал 
                    if not self.used_trial:
                        expire_at_date = date.today() + timedelta(days=app_settings.trial_days)  # Используем значение из настроек
                        self.is_trial = True
                        self.used_trial = True
                        self.expired_at = expire_at_date
                        logger.info(f"Назначен триал для {self.id} до {expire_at_date}")
                        # Send immediate notification about granted trial
                        try:
                            await notify_trial_granted(self)
                        except Exception as e_notify:
                            logger.error(f"Ошибка при отправке уведомления о предоставлении триала пользователю {self.id} в _ensure_remnawave_user: {e_notify}")
                    
                else:
                    # Если триал использован, устанавливаем дату на сегодня
                    expire_at_date = date.today()
                    logger.warning(f"Пользователь {self.id} уже использовал триал, дата истечения: {expire_at_date}")
                
                # Базовый лимит устройств
                hwid_limit = 1
                
                # Создаем пользователя в RemnaWave
                response = await remnawave.users.create_user(
                    username=f"{self.id}_TEST" if test_mode else str(self.id),
                    expire_at=expire_at_date,
                    telegram_id=self.id,
                    email=self.email,
                    description=f"Telegram: {self.name()}",
                    hwid_device_limit=hwid_limit
                )
                
                # Сохраняем UUID
                self.remnawave_uuid = response["response"]["uuid"]
                await self.save()
                
                logger.info(f"Пользователь {self.id} успешно создан в RemnaWave с UUID: {self.remnawave_uuid}")
                return True
            finally:
                # Гарантированно закрываем сессию клиента, даже при возникновении исключения
                await remnawave.close()
                logger.debug(f"Закрыта сессия клиента RemnaWave в _ensure_remnawave_user для пользователя {self.id}")
            
        except Exception as e:
            logger.error(f"Ошибка при создании пользователя {self.id} в RemnaWave: {str(e)}")
            # Не перевыбрасываем исключение, чтобы не блокировать работу приложения
            return False

    @classmethod
    async def get_user(
        cls, telegram_user: WebAppUser | User | None, referred_by: int = 0, utm: Optional[str] = None
    ):
        if not telegram_user:
            logger.error("get_user: telegram_user is None")
            return None

        try:
            user, is_new = await Users.update_or_create(
                id=telegram_user.id,
                defaults=dict(
                    username=telegram_user.username,
                    full_name=telegram_user.first_name
                    + (
                        f" {telegram_user.last_name}"
                        if telegram_user.last_name
                        else ""
                    ),
                    familyurl=get_family_url(telegram_user.id),
                ),
            )
            
            # Логируем, является ли пользователь новым
            logger.info(f"Пользователь {user.id} - новый: {is_new}, текущий UTM: {user.utm}")
            
            needs_save = False
            
            # Обработка UTM - устанавливаем только для новых пользователей
            if utm:
                logger.info(f"Получен параметр UTM: {utm} для пользователя {user.id}")
                if is_new:
                    user.utm = utm
                    needs_save = True
                    logger.info(f"Пользователь новый, устанавливаем UTM: {utm}")
                else:
                    logger.info(f"Пользователь {user.id} уже существует, пропускаем UTM")
            
            if is_new:
                # Обработка реферала (только для новых пользователей)
                referrer = None
                if referred_by and referred_by != user.id:  # Проверяем что пользователь не пытается стать своим рефералом
                    referrer = await cls.get_or_none(id=referred_by)
                    if referrer:
                        user.referred_by = referred_by
                        needs_save = True
                        logger.info(f"Пользователю {user.id} назначен реферер {referred_by}")

                # Отправляем уведомление о новом пользователе
                try:
                    await on_activated_bot(
                        user.id,
                        user.full_name,
                        referrer_id=referrer.id if referrer else None,
                        referrer_name=referrer.full_name if referrer else None,
                        utm=user.utm
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу о новом пользователе: {str(e)}")
            
            # Сохраняем пользователя, если были изменения (UTM или реферал)
            if needs_save:
                await user.save()
                logger.debug(f"Пользователь {user.id} сохранен после изменений. Текущий UTM: {user.utm}")
            else:
                logger.debug(f"Пользователь {user.id} не сохранялся, изменений не было. Текущий UTM: {user.utm}")
                
            await user.count_referrals()

            # Создаем пользователя в RemnaWave, если он еще не создан
            if is_new or not user.remnawave_uuid:
                await user._ensure_remnawave_user()
            # Schedule referral notifications for new users
            if is_new:
                from bloobcat.scheduler import schedule_user_tasks
                await schedule_user_tasks(user)

            return user
            
        except Exception as e:
            logger.error(f"Ошибка создания/обновления пользователя: {str(e)}")
            raise

    def expires(self) -> Optional[int]:
        # Если expired_at не установлен, возвращаем None
        if not self.expired_at:
            return None
        days_left = (self.expired_at - date.today()).days
        # Возвращаем 0, если подписка истекла сегодня или раньше
        return max(days_left, 0)

    def name(self) -> str:
        return f"@{self.username}" if self.username else self.full_name

    def referral_percent(self) -> int:
        ref_percent = self.custom_referral_percent
        if not self.custom_referral_percent:
            for i in referral_percent:
                if self.referrals >= i[0]:
                    ref_percent = i[1]
        return ref_percent

    async def extend_subscription(self, days: int):
        current_date = date.today()
        
        # Определяем базовую дату для продления.
        # Если подписка/триал активны (дата окончания в будущем), используем ее.
        # Иначе (подписка истекла или ее не было), используем текущую дату.
        start_date = max(self.expired_at, current_date) if self.expired_at else current_date
        
        self.expired_at = start_date + timedelta(days=days)
        
        await self.save()
        # Schedule subscription tasks whenever expired_at is updated
        from bloobcat.scheduler import schedule_user_tasks
        await schedule_user_tasks(self)

    async def referrer(self):
        if self.referred_by:
            return await Users.get(id=self.referred_by)
        return None

    async def count_referrals(self):
        referrals = await Users.filter(
            referred_by=self.id, is_registered=True
        ).count()
        self.referrals = referrals
        await self.save()

    async def delete(self, *args, **kwargs):
        """Удаляет пользователя: отзывает задачи Celery, удаляет в RemnaWave и из локальной БД"""
        # Cancel all scheduled asyncio tasks for this user
        from bloobcat.scheduler import cancel_user_tasks
        cancel_user_tasks(self.id)
        # Удаляем пользователя в RemnaWave
        if self.remnawave_uuid:
            from bloobcat.routes.remnawave.client import RemnaWaveClient
            from bloobcat.settings import remnawave_settings
            client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
            try:
                await client.users.delete_user(self.remnawave_uuid)
                logger.info(f"Пользователь {self.id} удален из RemnaWave")
            except Exception as e:
                logger.error(f"Ошибка при удалении пользователя {self.id} из RemnaWave: {e}")
            finally:
                await client.close()
        # Выполняем удаление из БД через оригинальный delete
        result = await super().delete(*args, **kwargs)
        logger.info(f"Пользователь {self.id} удален из локальной базе данных")
        return result

    async def save(self, *args, **kwargs):
        # Check previous expiration date
        old_expired_at = None
        if self.id is not None:
            orig = await Users.get_or_none(id=self.id)
            old_expired_at = orig.expired_at if orig else None
        # Save the user as usual
        await super().save(*args, **kwargs)
        if self.expired_at and self.expired_at != old_expired_at:
            # Немедленно обновляем RemnaWave при изменении expired_at
            if self.remnawave_uuid:
                try:
                    from bloobcat.routes.remnawave.client import RemnaWaveClient
                    from bloobcat.settings import remnawave_settings
                    remnawave_client = RemnaWaveClient(
                        remnawave_settings.url, 
                        remnawave_settings.token.get_secret_value()
                    )
                    
                    try:
                        await remnawave_client.users.update_user(
                            uuid=self.remnawave_uuid,
                            expireAt=self.expired_at
                        )
                        logger.debug(f"User {self.id} RemnaWave updated immediately: {old_expired_at} -> {self.expired_at}")
                    finally:
                        await remnawave_client.close()
                        
                except Exception as e:
                    logger.warning(f"User {self.id} failed to update RemnaWave immediately, will be synced by batch updater: {e}")
        
        # Всегда перепланируем задачи пользователя при любом сохранении
        try:
            from bloobcat.scheduler import schedule_user_tasks
            logger.debug(f"Rescheduling tasks for user {self.id} after save.")
            await schedule_user_tasks(self)
        except Exception as e:
            logger.error(f"Failed to reschedule tasks for user {self.id}: {e}")

    class PydanticMeta:
        computed = ["expires", "name", "referral_percent"]
        exclude = ["country_code"]
    
    @staticmethod
    async def get_blocked_users_stats() -> dict:
        """
        Возвращает статистику по заблокированным пользователям.
        """
        try:
            from bloobcat.settings import app_settings
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            
            MOSCOW = ZoneInfo("Europe/Moscow")
            
            total_blocked = await Users.filter(is_blocked=True).count()
            
            # Пользователи заблокированные в последние 24 часа
            last_24h = datetime.now(MOSCOW) - timedelta(hours=24)
            blocked_last_24h = await Users.filter(
                is_blocked=True,
                blocked_at__gte=last_24h
            ).count()
            
            # Пользователи готовые к очистке
            cutoff_date = datetime.now(MOSCOW) - timedelta(days=app_settings.blocked_user_cleanup_days)
            today = datetime.now(MOSCOW).date()
            subscription_cutoff_date = today - timedelta(days=app_settings.blocked_user_cleanup_days)
            
            # СЛУЧАЙ 1: Триальные пользователи (заблокированы > 7 дней назад)
            blocked_trial_ready = await Users.filter(
                is_blocked=True,
                blocked_at__lte=cutoff_date,
                is_trial=True
            ).count()
            
            # СЛУЧАЙ 2: Платные пользователи с истекшей подпиской (заблокированы > 7 дней И подписка истекла > 7 дней назад)
            blocked_paid_expired_ready = await Users.filter(
                is_blocked=True,
                blocked_at__lte=cutoff_date,
                is_trial=False,
                expired_at__lte=subscription_cutoff_date
            ).count()
            
            ready_for_cleanup = blocked_trial_ready + blocked_paid_expired_ready
            
            # Платные пользователи с активной подпиской (которые НЕ удаляются)
            blocked_paid_active = await Users.filter(
                is_blocked=True,
                blocked_at__lte=cutoff_date,
                is_trial=False,
                expired_at__gt=subscription_cutoff_date
            ).count()
            
            return {
                "total_blocked": total_blocked,
                "blocked_last_24h": blocked_last_24h,
                "ready_for_cleanup": ready_for_cleanup,
                "blocked_trial_ready": blocked_trial_ready,
                "blocked_paid_expired_ready": blocked_paid_expired_ready,
                "blocked_paid_active": blocked_paid_active,
                "cleanup_enabled": app_settings.cleanup_blocked_users_enabled,
                "cleanup_days": app_settings.blocked_user_cleanup_days,
                "max_failed_attempts": app_settings.blocked_user_max_failed_attempts
            }
        except Exception as e:
            logger.error(f"Error getting blocked users stats: {e}")
            return {
                "error": str(e)
            }


User_Pydantic = pydantic_model_creator(Users, name="User")

@register(Users)
class UsersModelAdmin(TortoiseModelAdmin):
    search_fields = ("username", "id", "full_name")
    list_display = (
        "username",
        "id",
        "full_name",
        "expired_at",
        "balance",
        "utm",
        "active_tariff_id",
        "hwid_limit",
        "is_admin",
        "is_partner",
        "is_blocked",
        "blocked_at",
    )
    readonly_fields = (
        "id",
        "registration_date",
        "activation_date",
        "referrals",
        "username",
        "full_name",
    )
    fields = (
        "id",
        "username",
        "full_name",
        "expired_at",
        "is_registered",
        "balance",
        "referred_by",
        "is_admin",
        "is_partner",
        "custom_referral_percent",
        "registration_date",
        "referrals",
        "is_subscribed",
        "utm",
        "renew_id",
        "connected_at",
        "email",
        "created_at",
        "is_trial",
        "used_trial",
        "notification_2h_sent",
        "notification_24h_sent",
        "remnawave_uuid",
        "familyurl",
        "active_tariff",
        "hwid_limit",
        "is_blocked",
        "blocked_at",
        "last_failed_message_at",
        "failed_message_count",
    )
    search_help_text = "Юзернейм, имя, айди"
    verbose_name = "Пользователи"
    verbose_name_plural = "Пользователи"

    async def save_model(self, pk, form_data=None):
        """
        Переопределенный метод сохранения пользователя.
        
        После сохранения в БД обновляет дату истечения и лимит устройств в RemnaWave.
        
        :param pk: Первичный ключ пользователя (ID)
        :param form_data: Данные формы с изменениями
        """
        original_obj = None
        original_expired_at = None
        original_hwid_limit = None
        
        if pk:
            original_obj = await Users.get_or_none(id=pk)
            if original_obj:
                original_expired_at = original_obj.expired_at
                original_hwid_limit = original_obj.hwid_limit

        result = await super().save_model(pk, form_data)
        
        obj = await Users.get(id=pk)
        
        logger.debug(f"Пользователь {obj.id} сохранен. Проверяем необходимость обновления в RemnaWave")
        
        if obj.remnawave_uuid:
            updates_needed = {}
            
            # Проверяем изменение даты истечения
            if obj.expired_at and obj.expired_at != original_expired_at:
                from datetime import date
                today = date.today()
                
                if obj.expired_at < today:
                    logger.warning(f"Дата истечения {obj.expired_at} в прошлом. Пропускаем обновление даты.")
                else:
                    updates_needed["expireAt"] = obj.expired_at
                    logger.debug(f"Дата истечения изменилась: {original_expired_at} -> {obj.expired_at}")
            
            # Проверяем изменение hwid_limit
            if obj.hwid_limit is not None and obj.hwid_limit != original_hwid_limit:
                updates_needed["hwidDeviceLimit"] = obj.hwid_limit
                logger.debug(f"Лимит устройств изменился: {original_hwid_limit} -> {obj.hwid_limit}")
            
            # Обновляем RemnaWave, если есть изменения
            if updates_needed:
                try:
                    from bloobcat.routes.remnawave.client import RemnaWaveClient
                    client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
                    try:
                        await client.users.update_user(obj.remnawave_uuid, **updates_needed)
                        logger.debug(f"Данные для пользователя {obj.id} обновлены в RemnaWave: {updates_needed}")
                    finally:
                        await client.close()
                except Exception as e:
                    logger.error(f"Ошибка при обновлении данных в RemnaWave для пользователя {obj.id}: {e}", 
                                exc_info=True)
        else:
            logger.warning(f"Пользователь {obj.id} не имеет UUID в RemnaWave, обновления пропускаются")
        
        return result

    async def delete_model(self, pk: int):
        """Удаление пользователя через админку: используем переопределённый delete() модели"""
        # Получаем пользователя
        user_obj = await Users.get_or_none(id=pk)
        if user_obj:
            # Удаляем через метод delete(), где отзываются задачи Celery и удаляется из RemnaWave
            await user_obj.delete()
            logger.info(f"Пользователь {pk} удалён через админку с revocation Celery и RemnaWave")
        else:
            logger.warning(f"Пользователь с ID {pk} не найден при удалении через админку")
        # Возвращаем стандартный ответ админки
        await super().delete_model(pk)

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

from bloobcat.settings import remnawave_settings
from bloobcat.logger import get_logger

from bloobcat.db.active_tariff import ActiveTariffs

import zlib

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
    referred_by = fields.IntField(default=0)
    is_admin = fields.BooleanField(default=False)
    custom_referral_percent = fields.IntField(default=0)
    registration_date = fields.DatetimeField(auto_now_add=True)
    referrals = fields.IntField(default=0)
    is_subscribed = fields.BooleanField(default=False)
    utm = fields.CharField(max_length=100, null=True)
    renew_id = fields.CharField(max_length=100, null=True)
    connected_at = fields.DatetimeField(null=True)
    email = fields.CharField(max_length=255, null=True)
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
                        expire_at_date = date.today() + timedelta(days=3)  # 3 дня триал
                        self.is_trial = True
                        self.used_trial = True
                        self.expired_at = expire_at_date
                        logger.info(f"Назначен триал для {self.id} до {expire_at_date}")
                    else:
                        # Если триал использован, устанавливаем дату на сегодня
                        expire_at_date = date.today()
                        logger.warning(f"Пользователь {self.id} уже использовал триал, дата истечения: {expire_at_date}")
                
                # Базовый лимит устройств
                hwid_limit = 1
                
                # Создаем пользователя в RemnaWave
                response = await remnawave.users.create_user(
                    username=str(self.id),
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
            return

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
            
            # Обработка UTM - устанавливаем только если пользователь новый ИЛИ у него еще нет UTM
            if utm:
                logger.info(f"Получен параметр UTM: {utm} для пользователя {user.id}")
                if is_new:
                    user.utm = utm
                    needs_save = True
                    logger.info(f"Пользователь новый, устанавливаем UTM: {utm}")
                elif user.utm is None:
                    user.utm = utm
                    needs_save = True
                    logger.info(f"У пользователя нет UTM, устанавливаем: {utm}")
                else:
                    logger.info(f"У пользователя уже есть UTM: {user.utm}, сохраняем её (не перезаписываем)")
            
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
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу о новом пользователе: {str(e)}")
            
            # Сохраняем пользователя, если были изменения (UTM или реферал)
            if needs_save:
                await user.save()
                logger.info(f"Пользователь {user.id} сохранен после изменений. Текущий UTM: {user.utm}")
            else:
                logger.info(f"Пользователь {user.id} не сохранялся, изменений не было. Текущий UTM: {user.utm}")
                
            await user.count_referrals()

            # Создаем пользователя в RemnaWave, если он еще не создан
            # Это будет происходить при первом обращении к /user эндпоинту
            if is_new or not user.remnawave_uuid:
                await user._ensure_remnawave_user()
            
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
        if self.expired_at and self.expired_at > current_date:
            # Если подписка активна, считаем от даты окончания текущей подписки
            self.expired_at = self.expired_at + timedelta(days=days)
        else:
            # Если подписка неактивна, считаем от текущей даты
            self.expired_at = current_date + timedelta(days=days)
        await self.save()

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

    async def delete_model(self):
        """Удаляет пользователя из RemnaWave и локальной базы данных"""
        try:
            if self.remnawave_uuid:
                from bloobcat.routes.remnawave.client import RemnaWaveClient
                from bloobcat.settings import remnawave_settings

                client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
                await client.users.delete_user(self.remnawave_uuid)
                logger.info(f"Пользователь {self.id} удален из RemnaWave")

            # Вызываем стандартный метод delete модели Tortoise
            await self.delete()
            logger.info(f"Пользователь {self.id} удален из локальной базы данных")
        except Exception as e:
            logger.error(f"Ошибка при удалении пользователя {self.id}: {e}")
            raise

    class PydanticMeta:
        computed = ["expires", "name", "referral_percent"]
        exclude = ["country_code"]


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
    )
    search_help_text = "Юзернейм, имя, айди"
    verbose_name = "Пользователи"
    verbose_name_plural = "Пользователи"

    async def save_model(self, pk, form_data=None):
        """
        Переопределенный метод сохранения пользователя.
        
        После сохранения в БД обновляет дату истечения подписки в RemnaWave.
        
        :param pk: Первичный ключ пользователя (ID)
        :param form_data: Данные формы с изменениями
        """
        # Получаем исходного пользователя по ID
        original_obj = None
        original_expired_at = None
        
        if pk:
            original_obj = await Users.get_or_none(id=pk)
            if original_obj:
                original_expired_at = original_obj.expired_at
        
        # Вызываем стандартный метод сохранения (он может возвращать dict)
        result = await super().save_model(pk, form_data)
        
        # Получаем обновленный объект пользователя из БД
        obj = await Users.get(id=pk)
        
        logger.info(f"Пользователь {obj.id} сохранен. Проверяем необходимость обновления в RemnaWave")
        
        # Проверяем, изменилась ли дата истечения и есть ли UUID RemnaWave
        if obj.remnawave_uuid and obj.expired_at and obj.expired_at != original_expired_at:
            # Проверка, не является ли дата в прошлом
            from datetime import date
            today = date.today()
            
            if obj.expired_at < today:
                logger.warning(f"Дата истечения {obj.expired_at} находится в прошлом. RemnaWave не принимает такие даты. Пропускаем обновление.")
            else:
                logger.info(f"Дата истечения изменилась: {original_expired_at} -> {obj.expired_at}. Обновляем в RemnaWave")
                
                try:
                    # Импортируем здесь, чтобы избежать циклического импорта
                    from bloobcat.routes.remnawave.client import RemnaWaveClient
                    from datetime import datetime, time, timezone
                    
                    # Подготавливаем дату в формате API RemnaWave
                    expire_at_dt = datetime.combine(obj.expired_at, time.max, tzinfo=timezone.utc)
                    expire_at_dt_str = expire_at_dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + 'Z'
                    
                    # Создаем клиент и обновляем пользователя
                    client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
                    try:
                        await client.users.update_user(obj.remnawave_uuid, expireAt=expire_at_dt_str)
                        logger.info(f"Дата истечения для пользователя {obj.id} успешно обновлена в RemnaWave")
                    finally:
                        # Гарантированно закрываем сессию клиента
                        await client.close()
                        logger.debug(f"Закрыта сессия клиента RemnaWave после обновления даты истечения")
                except Exception as e:
                    logger.error(f"Ошибка при обновлении даты истечения в RemnaWave для пользователя {obj.id}: {str(e)}", 
                                exc_info=True)
                    # Не перевыбрасываем исключение, чтобы не прерывать работу админ-панели
        
        # Возвращаем результат от super().save_model(), который FastAPI может сериализовать
        return result

    async def delete_model(self, pk: int):
        """Переопределенный метод удаления пользователя.

        Сначала получает объект пользователя по pk, затем пытается удалить
        пользователя из RemnaWave и после этого удаляет из БД.
        """
        logger.info(f"Запрос на удаление пользователя с ID {pk} из админ-панели")
        
        # Получаем объект пользователя из БД
        user_obj = await Users.get_or_none(id=pk)
        
        if not user_obj:
            logger.warning(f"Пользователь с ID {pk} не найден в БД. Удаление невозможно.")
            # Вызываем super, чтобы fastadmin получил ожидаемый результат.
            await super().delete_model(pk)
            return
            
        logger.info(f"Найден пользователь {user_obj.id} ({user_obj.name()}) для удаления")

        # Пытаемся удалить из RemnaWave
        if user_obj.remnawave_uuid:
            remnawave = None  # Объявляем переменную в более широкой области
            try:
                # Импортируем RemnaWaveClient здесь, чтобы избежать циклического импорта
                from bloobcat.routes.remnawave.client import RemnaWaveClient
                # Создаем экземпляр клиента RemnaWave
                remnawave = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
                logger.info(f"Попытка удаления пользователя {user_obj.id} (UUID: {user_obj.remnawave_uuid}) из RemnaWave...")
                await remnawave.users.delete_user(user_obj.remnawave_uuid)
                logger.info(f"Пользователь {user_obj.id} успешно удален/отсутствовал в RemnaWave.")
            except Exception as e:
                # Логируем ошибку, но продолжаем удаление из локальной БД
                logger.error(
                    f"Исключение при попытке удаления пользователя {user_obj.id} (UUID: {user_obj.remnawave_uuid}) из RemnaWave: {str(e)}. "
                    f"Пользователь все равно будет удален из локальной БД.",
                    exc_info=True # Добавляем трейсбек для детальной диагностики
                )
            finally:
                # Гарантированно закрываем сессию клиента в любом случае
                if remnawave:
                    await remnawave.close()
                    logger.debug(f"Закрыта сессия клиента RemnaWave после удаления пользователя {user_obj.id}")
        else:
            logger.warning(f"У пользователя {user_obj.id} отсутствует remnawave_uuid. Пропускаем удаление из RemnaWave.")
        
        # Выполняем стандартное удаление из базы данных, передавая pk
        await super().delete_model(pk)
        logger.info(f"Пользователь {user_obj.id} ({user_obj.name()}) успешно удален из локальной БД.")

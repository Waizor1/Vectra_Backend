import zlib
from datetime import date, timedelta
from typing import TYPE_CHECKING, Optional

from aiogram.types import User
from aiogram.utils.web_app import WebAppUser
from fastadmin import TortoiseModelAdmin, register
from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator

from cyberdog.bot.notifications.admin import on_activated_bot
from cyberdog.config import referral_percent

# Используем TYPE_CHECKING для MarzbanClient, чтобы избежать циклического импорта
if TYPE_CHECKING:
    from cyberdog.routes.marzban.client import MarzbanClient

import logging

logger = logging.getLogger(__name__)


def crc32(a: str):
    return format(zlib.crc32(str(a).encode("utf-8")), "08x")


def get_connect_url(user_id) -> str:
    return (
        crc32(f"{user_id}connect") + crc32(f"connect {user_id}") + "cyberdog"
    )


class Users(models.Model):
    id = fields.BigIntField(primary_key=True)
    username = fields.CharField(max_length=100, null=True)
    full_name = fields.CharField(max_length=1000)
    expired_at = fields.DateField(null=True)
    is_registered = fields.BooleanField(default=False)
    connect_url = fields.CharField(max_length=100, null=True)
    balance = fields.IntField(default=0)
    referred_by = fields.IntField(default=0)
    is_admin = fields.BooleanField(default=False)
    custom_referral_percent = fields.IntField(default=0)
    registration_date = fields.DatetimeField(auto_now_add=True)
    referrals = fields.IntField(default=0)
    is_subscribed = fields.BooleanField(default=False)
    is_sended_notification_connect = fields.BooleanField(default=False)
    utm = fields.CharField(max_length=100, null=True)
    renew_id = fields.CharField(max_length=100, null=True)
    last_action = fields.CharField(max_length=100, null=True)
    tv_connect = fields.CharField(max_length=5, null=True)
    connected_at = fields.DatetimeField(null=True)
    email = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    is_trial = fields.BooleanField(default=False)
    used_trial = fields.BooleanField(default=False)
    notification_2h_sent = fields.BooleanField(default=False)
    notification_24h_sent = fields.BooleanField(default=False)

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
                    connect_url=get_connect_url(telegram_user.id),
                    tv_connect=crc32(get_connect_url(telegram_user.id))[::-1][:5],
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
                referrer = await cls.get_or_none(id=referred_by)
                if referrer and referrer.id != user.id:
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

    class PydanticMeta:
        computed = ["expires", "name", "referral_percent"]
        exclude = ["country_code"]


User_Pydantic = pydantic_model_creator(Users, name="User")

# Создаем экземпляр клиента Marzban один раз на уровне модуля
# Это ленивая инициализация, фактическое создание произойдет при первом обращении
marzban_client_instance: Optional["MarzbanClient"] = None

def get_marzban_client() -> "MarzbanClient":
    """Возвращает синглтон экземпляр MarzbanClient."""
    global marzban_client_instance
    if marzban_client_instance is None:
        from cyberdog.routes.marzban.client import MarzbanClient # Импорт здесь
        marzban_client_instance = MarzbanClient()
        logger.info("Экземпляр MarzbanClient создан для UsersModelAdmin")
    return marzban_client_instance

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
    )
    readonly_fields = (
        "id",
        "connect_url",
        "registration_date",
        "activation_date",
        "referrals",
        "username",
        "full_name",
    )

    search_help_text = "Юзернейм, имя, айди"
    verbose_name = "Пользователи"
    verbose_name_plural = "Пользователи"

    async def delete_model(self, pk: int):
        """Переопределенный метод удаления пользователя.

        Сначала получает объект пользователя по pk, затем пытается удалить
        пользователя из Marzban и после этого удаляет из БД.
        """
        logger.info(f"Запрос на удаление пользователя с ID {pk} из админ-панели")
        
        # Получаем объект пользователя из БД
        user_obj = await Users.get_or_none(id=pk)
        
        if not user_obj:
            logger.warning(f"Пользователь с ID {pk} не найден в БД. Удаление невозможно.")
            # Возможно, стоит вернуть ошибку или просто прервать выполнение
            # super().delete_model(pk) все равно может сработать, если fastadmin 
            # не проверяет существование объекта перед вызовом delete_model.
            # На всякий случай вызываем super, чтобы fastadmin получил ожидаемый результат.
            await super().delete_model(pk)
            return
            
        logger.info(f"Найден пользователь {user_obj.id} ({user_obj.name()}) для удаления")

        # Пытаемся удалить из Marzban
        try:
            marzban = get_marzban_client()
            success = await marzban.users.delete_user(user_obj)
            if not success:
                logger.error(
                    f"Не удалось удалить пользователя {user_obj.id} из Marzban, "
                    f"но он все равно будет удален из локальной БД."
                )
            else:
                logger.info(f"Пользователь {user_obj.id} успешно удален/отсутствовал в Marzban.")
        except Exception as e:
            logger.error(
                f"Исключение при попытке удаления пользователя {user_obj.id} из Marzban: {str(e)}. "
                f"Пользователь все равно будет удален из локальной БД.",
                exc_info=True
            )
        
        # Выполняем стандартное удаление из базы данных, передавая pk
        await super().delete_model(pk)
        logger.info(f"Пользователь {user_obj.id} ({user_obj.name()}) успешно удален из локальной БД.")

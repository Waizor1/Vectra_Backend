import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Any, Union

from aiogram.types import User
from aiogram.utils.web_app import WebAppUser
from fastapi import Request
from fastadmin import TortoiseModelAdmin, register
from pydantic import BaseModel as FastAdminBaseModel, Field
from tortoise import fields, models
from tortoise.contrib.pydantic import pydantic_model_creator

from bloobcat.bot.notifications.admin import on_activated_bot
from bloobcat.config import referral_percent

import logging

from bloobcat.settings import remnawave_settings, test_mode, app_settings
from bloobcat.logger import get_logger
from bloobcat.services.trial_lte import read_trial_lte_limit_gb

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.fk_guards import (
    ensure_active_tariffs_fk_cascade,
    ensure_notification_marks_fk_cascade,
    ensure_promo_usages_fk_cascade,
    ensure_users_referred_by_fk_set_null,
)
import zlib

# Import for new trial granted notification
from bloobcat.bot.notifications.trial.granted import notify_trial_granted

logger = get_logger("users_db")

_REMNAWAVE_USER_LOCKS: dict[int, asyncio.Lock] = {}
_REMNAWAVE_USER_LOCKS_GUARD = asyncio.Lock()
WEB_USER_ID_FLOOR = 8_000_000_000_000_000
REMNAWAVE_USERNAME_PREFIX = "VECTRA_"
REMNAWAVE_USERNAME_MAX_LENGTH = 36
_REMNAWAVE_USERNAME_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_]+")


async def _get_remnawave_user_lock(user_id: int) -> asyncio.Lock:
    async with _REMNAWAVE_USER_LOCKS_GUARD:
        lock = _REMNAWAVE_USER_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _REMNAWAVE_USER_LOCKS[user_id] = lock
        return lock


def normalize_date(val: Optional[Union[date, datetime]]) -> Optional[date]:
    """Нормализует datetime/date к date для безопасного сравнения.

    Args:
        val: datetime.datetime или datetime.date, либо None

    Returns:
        datetime.date или None
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    # Fallback для неожиданных типов (не должно происходить при корректной типизации)
    return val


def build_vectra_remnawave_username(raw_username: str) -> str:
    """Return a RemnaWave-safe Vectra username without leaking plain service IDs."""

    normalized = _REMNAWAVE_USERNAME_SAFE_CHARS.sub(
        "_", str(raw_username or "").strip()
    ).strip("_")
    if not normalized:
        normalized = "user"
    if normalized.startswith(REMNAWAVE_USERNAME_PREFIX):
        candidate = normalized
    else:
        candidate = f"{REMNAWAVE_USERNAME_PREFIX}{normalized}"
    return candidate[:REMNAWAVE_USERNAME_MAX_LENGTH]


def legacy_remnawave_username_for_user_id(user_id: int, *, test: bool = False) -> str:
    is_web_user = int(user_id) >= WEB_USER_ID_FLOOR
    if test and is_web_user:
        return f"web_{user_id}_TEST"
    if test:
        return f"{user_id}_TEST"
    if is_web_user:
        return f"web_{user_id}"
    return str(user_id)


def remnawave_username_for_user_id(user_id: int, *, test: bool = False) -> str:
    return build_vectra_remnawave_username(
        legacy_remnawave_username_for_user_id(user_id, test=test)
    )


def crc32(a: str):
    return format(zlib.crc32(str(a).encode("utf-8")), "08x")


def get_family_url(user_id) -> str:
    return crc32(f"{user_id}family") + crc32(f"family {user_id}") + "blubcat"


class Users(models.Model):
    id = fields.BigIntField(primary_key=True)
    username = fields.CharField(max_length=100, null=True)
    full_name = fields.CharField(max_length=1000)
    expired_at = fields.DateField(null=True)
    is_registered = fields.BooleanField(default=False)
    key_activated = fields.BooleanField(
        default=False,
        description="Активирован ли ключ (появился первый HWID в RemnaWave)",
    )
    balance = fields.IntField(default=0)
    referred_by = fields.BigIntField(null=True, default=None)
    is_admin = fields.BooleanField(default=False)
    is_partner = fields.BooleanField(default=False)
    custom_referral_percent = fields.IntField(default=0)
    registration_date = fields.DatetimeField(auto_now_add=True)
    referrals = fields.IntField(default=0)
    # Total referral bonus (in days) earned by the user.
    # This is NOT a "money balance" - it exists to match the Mini App referral rules (days-based rewards).
    referral_bonus_days_total = fields.IntField(default=0)
    # Guard: referral rewards are applied only for the first successful payment of a referred user.
    referral_first_payment_rewarded = fields.BooleanField(default=False)
    is_subscribed = fields.BooleanField(default=False)
    utm = fields.CharField(max_length=100, null=True)
    renew_id = fields.CharField(max_length=100, null=True)
    connected_at = fields.DatetimeField(null=True)
    email = fields.CharField(max_length=255, null=True)
    email_notifications_enabled = fields.BooleanField(default=True)
    language_code = fields.CharField(
        max_length=5,
        default="ru",
        description="Код языка пользователя для локализации уведомлений",
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    is_trial = fields.BooleanField(default=False)
    used_trial = fields.BooleanField(default=False)
    trial_started_at = fields.DatetimeField(
        null=True,
        description="Когда пользователю был выдан пробный период",
    )
    remnawave_uuid = fields.UUIDField(null=True)  # UUID пользователя в RemnaWave
    last_hwid_reset = fields.DatetimeField(
        null=True,
        description="Дата и время последнего ручного отключения HWID устройств",
    )
    familyurl = fields.CharField(max_length=100, null=True)
    active_tariff: fields.ForeignKeyNullableRelation["ActiveTariffs"] = (
        fields.ForeignKeyField(
            "models.ActiveTariffs",
            related_name="users",
            null=True,
            on_delete=fields.SET_NULL,
            description="ID активного тарифа пользователя",
        )
    )
    hwid_limit = fields.IntField(
        null=True,
        description="Личный лимит устройств (переопределяет тариф и настройки панели)",
    )
    lte_gb_total = fields.IntField(
        null=True, description="Личный LTE лимит (GB), переопределяет тариф"
    )
    is_blocked = fields.BooleanField(
        default=False, description="Пользователь заблокировал бота"
    )
    blocked_at = fields.DatetimeField(null=True, description="Дата и время блокировки")
    last_failed_message_at = fields.DatetimeField(
        null=True, description="Последняя неуспешная попытка отправки"
    )
    failed_message_count = fields.IntField(
        default=0, description="Количество неуспешных попыток подряд"
    )
    auth_token_version = fields.IntField(
        default=0,
        description="Версия auth-токенов пользователя для серверной ревокации сессий",
    )
    device_per_user_enabled = fields.BooleanField(
        null=True,
        description="Per-user device-per-user override; null means use global rollout flag",
    )
    temp_setup_token = fields.CharField(max_length=255, null=True, unique=True)
    temp_setup_expires_at = fields.DatetimeField(null=True)
    temp_setup_device_id = fields.IntField(
        null=True,
        description="Temporary setup UserDevice id for the device-per-user onboarding link",
    )
    # Колесо призов: количество доступных попыток для пользователя
    prize_wheel_attempts = fields.IntField(
        default=0, description="Доступные попытки на колесе призов"
    )

    def is_device_per_user_enabled(self) -> bool:
        if self.device_per_user_enabled is not None:
            return bool(self.device_per_user_enabled)
        return bool(getattr(app_settings, "device_per_user_enabled", False))

    @staticmethod
    def _extract_remnawave_user(payload: Any) -> Optional[dict]:
        if not isinstance(payload, dict):
            return None
        response = payload.get("response")
        if isinstance(response, dict):
            return response
        return None

    @staticmethod
    def _is_remnawave_username_exists_error(error_text: str) -> bool:
        lowered = (error_text or "").lower()
        return (
            "already exists" in lowered
            or "a019" in lowered
            or "user username already exists" in lowered
        )

    @staticmethod
    def _is_remnawave_not_found_error(error_text: str) -> bool:
        normalized = "".join(ch for ch in (error_text or "").lower() if ch.isalnum())
        explicit_signals = (
            "a025",
            "a063",
            "userwithspecifiedparamsnotfound",
            "usernotfound",
        )
        return any(signal in normalized for signal in explicit_signals)

    @staticmethod
    def _normalize_hwid_limit_value(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 1
        return max(1, parsed)

    @staticmethod
    def _parse_remnawave_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            raw_value = str(value).strip()
            if not raw_value:
                return None
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

    @classmethod
    def _extract_existing_remnawave_connection_at(
        cls, remote_user: Any
    ) -> Optional[datetime]:
        """Return historical connection time for an adopted RemnaWave user.

        When a fresh local DB rebinds to an already-existing RemnaWave user,
        `userTraffic.onlineAt`/`firstConnectedAt` describes historical remote
        activity, not a new local first connection. Import that state silently
        so the periodic checker does not emit a misleading first-activation log.
        """
        if not isinstance(remote_user, dict):
            return None

        user_traffic = remote_user.get("userTraffic") or {}
        if not isinstance(user_traffic, dict):
            user_traffic = {}

        for candidate in (
            user_traffic.get("firstConnectedAt"),
            remote_user.get("firstConnectedAt"),
            user_traffic.get("onlineAt"),
            remote_user.get("onlineAt"),
        ):
            parsed = cls._parse_remnawave_datetime(candidate)
            if parsed:
                return parsed
        return None

    async def _remote_user_has_hwid_device(
        self, remnawave: Any, remnawave_uuid: Any
    ) -> bool:
        if not remnawave_uuid:
            return False
        try:
            from bloobcat.routes.remnawave.hwid_utils import (
                extract_hwid_from_device,
                parse_remnawave_devices,
            )

            raw_devices = await remnawave.users.get_user_hwid_devices(
                str(remnawave_uuid)
            )
            return any(
                extract_hwid_from_device(item)
                for item in parse_remnawave_devices(raw_devices)
            )
        except Exception as exc:
            logger.warning(
                "Не удалось проверить HWID при rebind local user=%s uuid=%s: %s",
                self.id,
                remnawave_uuid,
                exc,
            )
            return False

    @classmethod
    def _apply_existing_remnawave_connection_state(
        cls, current_user: "Users", remote_user: Any, *, has_hwid_device: bool
    ) -> list[str]:
        connection_at = cls._extract_existing_remnawave_connection_at(remote_user)
        update_fields: list[str] = []

        if connection_at and not current_user.connected_at:
            current_user.connected_at = connection_at
            update_fields.append("connected_at")

        if has_hwid_device and not current_user.key_activated:
            current_user.key_activated = True
            update_fields.append("key_activated")
        if has_hwid_device and not current_user.is_registered:
            current_user.is_registered = True
            update_fields.append("is_registered")
        return update_fields

    async def _find_existing_remnawave_user(
        self,
        remnawave: Any,
        base_username: str,
        legacy_usernames: Optional[list[str]] = None,
    ) -> Optional[dict]:
        candidates: list[dict] = []
        username_candidates = list(
            dict.fromkeys([base_username, *(legacy_usernames or [])])
        )

        async def _collect(payload_coro, source: str):
            try:
                payload = await payload_coro
            except Exception as exc:
                err_text = str(exc)
                # Endpoint может отсутствовать в старых версиях панели или пользователь не найден.
                if self._is_remnawave_not_found_error(err_text):
                    return
                logger.debug(
                    "Не удалось получить пользователя RemnaWave (%s) для local user=%s: %s",
                    source,
                    self.id,
                    err_text,
                )
                return

            user_payload = self._extract_remnawave_user(payload)
            if user_payload:
                candidates.append(user_payload)

        # Быстрые точечные lookup'и
        await _collect(remnawave.users.get_user_by_telegram_id(self.id), "telegram_id")
        if self.email:
            await _collect(remnawave.users.get_user_by_email(self.email), "email")
        for username_candidate in username_candidates:
            await _collect(
                remnawave.users.get_user_by_username(username_candidate),
                "username",
            )

        # Fallback по списку пользователей (на случай старой панели без точечных endpoint'ов)
        try:
            page_size = 100
            start_index = 0
            total_users = None
            max_pages = 20
            pages_fetched = 0
            normalized_email = (self.email or "").strip().lower()
            username_prefixes = [
                f"{username_candidate}_"
                for username_candidate in username_candidates
                if username_candidate
            ]

            while pages_fetched < max_pages and (
                total_users is None or start_index < total_users
            ):
                response = await remnawave.users.get_users(
                    size=page_size, start=start_index
                )
                body = response.get("response") if isinstance(response, dict) else None
                if not isinstance(body, dict):
                    break

                users_list = body.get("users") or []
                if not isinstance(users_list, list):
                    break

                if total_users is None:
                    total_users = body.get("total")

                for remote_user in users_list:
                    if not isinstance(remote_user, dict):
                        continue
                    username = str(remote_user.get("username") or "")
                    telegram_id = remote_user.get("telegramId")
                    email = str(remote_user.get("email") or "").strip().lower()
                    if (
                        str(telegram_id) == str(self.id)
                        or username in username_candidates
                        or any(
                            username.startswith(prefix)
                            for prefix in username_prefixes
                        )
                        or (normalized_email and email and email == normalized_email)
                    ):
                        candidates.append(remote_user)

                pages_fetched += 1
                if len(users_list) < page_size:
                    break
                start_index += page_size
        except Exception as exc:
            logger.debug(
                "Не удалось выполнить fallback-поиск пользователя RemnaWave по списку для local user=%s: %s",
                self.id,
                exc,
            )

        # Дедупликация по UUID и выбор лучшего кандидата
        unique_candidates: dict[str, dict] = {}
        for candidate in candidates:
            candidate_uuid = candidate.get("uuid")
            if candidate_uuid:
                unique_candidates[str(candidate_uuid)] = candidate

        best_user = None
        best_score = -1
        for candidate in unique_candidates.values():
            score = 0
            candidate_username = str(candidate.get("username") or "")
            candidate_telegram_id = candidate.get("telegramId")
            candidate_email = str(candidate.get("email") or "").strip().lower()

            if str(candidate_telegram_id) == str(self.id):
                score += 100
            if candidate_username == base_username:
                score += 50
            elif candidate_username.startswith(f"{base_username}_"):
                score += 20
            elif candidate_username in (legacy_usernames or []):
                score += 15
            elif any(
                candidate_username.startswith(f"{legacy_username}_")
                for legacy_username in (legacy_usernames or [])
            ):
                score += 10
            if (
                self.email
                and candidate_email
                and candidate_email == self.email.strip().lower()
            ):
                score += 30

            if score > best_score:
                best_user = candidate
                best_score = score

        return best_user

    @classmethod
    async def _grant_trial_if_unclaimed(cls, user_id: int, trial_until: date) -> bool:
        rows_updated = await cls.filter(
            id=user_id,
            expired_at__isnull=True,
            used_trial=False,
        ).update(
            is_trial=True,
            used_trial=True,
            expired_at=trial_until,
            trial_started_at=datetime.now(timezone.utc),
        )
        return bool(rows_updated)

    async def _ensure_remnawave_user(self) -> bool:
        """
        Создает пользователя в RemnaWave, если он еще не создан.
        Возвращает True, если пользователь был создан/перепривязан, False - если уже существовал или не удалось.
        """
        lock = await _get_remnawave_user_lock(int(self.id))
        async with lock:
            try:
                current_user = await Users.get_or_none(id=self.id) or self
                if current_user.remnawave_uuid:
                    normalized_existing_hwid_limit = self._normalize_hwid_limit_value(
                        current_user.hwid_limit
                    )
                    if current_user.hwid_limit != normalized_existing_hwid_limit:
                        current_user.hwid_limit = normalized_existing_hwid_limit
                        await current_user.save(update_fields=["hwid_limit"])
                    self.remnawave_uuid = current_user.remnawave_uuid
                    self.hwid_limit = normalized_existing_hwid_limit
                    logger.info(
                        f"Пользователь {self.id} уже имеет UUID в RemnaWave: {self.remnawave_uuid}"
                    )
                    return False

                from bloobcat.routes.remnawave.client import RemnaWaveClient
                from bloobcat.settings import remnawave_settings

                remnawave = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value(),
                )
                try:
                    expire_at_date = current_user.expired_at
                    today = date.today()
                    if expire_at_date is None:
                        if not current_user.used_trial:
                            trial_until = today + timedelta(
                                days=app_settings.trial_days
                            )
                            trial_granted = await Users._grant_trial_if_unclaimed(
                                int(current_user.id),
                                trial_until,
                            )
                            if trial_granted:
                                expire_at_date = trial_until
                                current_user.trial_started_at = datetime.now(timezone.utc)
                                current_user.is_trial = True
                                current_user.used_trial = True
                                current_user.expired_at = expire_at_date
                                logger.info(
                                    f"Назначен триал для {self.id} до {expire_at_date}"
                                )
                                try:
                                    await notify_trial_granted(current_user)
                                except Exception as e_notify:
                                    logger.error(
                                        "Ошибка при отправке уведомления о предоставлении триала "
                                        f"пользователю {self.id} в _ensure_remnawave_user: {e_notify}"
                                    )
                            else:
                                refreshed_user = await Users.get_or_none(
                                    id=current_user.id
                                )
                                if refreshed_user:
                                    current_user.is_trial = refreshed_user.is_trial
                                    current_user.used_trial = refreshed_user.used_trial
                                    current_user.trial_started_at = (
                                        getattr(
                                            refreshed_user,
                                            "trial_started_at",
                                            getattr(
                                                current_user,
                                                "trial_started_at",
                                                None,
                                            ),
                                        )
                                    )
                                    current_user.expired_at = refreshed_user.expired_at
                                    expire_at_date = refreshed_user.expired_at
                    else:
                        expire_at_date = max(expire_at_date, today)
                        if current_user.expired_at < today:
                            logger.info(
                                "Пользователь %s имеет истекшую подписку (%s), expire_at для RemnaWave зажат до today=%s",
                                self.id,
                                current_user.expired_at,
                                today,
                            )
                        else:
                            logger.info(
                                "Пользователь %s сохраняет существующую дату подписки для RemnaWave: %s",
                                self.id,
                                expire_at_date,
                            )

                    normalized_hwid_limit = self._normalize_hwid_limit_value(
                        current_user.hwid_limit
                    )
                    if current_user.hwid_limit != normalized_hwid_limit:
                        current_user.hwid_limit = normalized_hwid_limit
                    self.hwid_limit = normalized_hwid_limit

                    internal_squads = []
                    if remnawave_settings.default_internal_squad_uuid:
                        internal_squads.append(
                            remnawave_settings.default_internal_squad_uuid
                        )
                    lte_uuid = remnawave_settings.lte_internal_squad_uuid
                    if lte_uuid:
                        lte_allowed = False
                        if current_user.is_trial:
                            lte_allowed = (await read_trial_lte_limit_gb()) > 0
                        elif current_user.active_tariff_id:
                            active_tariff = await ActiveTariffs.get_or_none(
                                id=current_user.active_tariff_id
                            )
                            effective_lte_total = (
                                current_user.lte_gb_total
                                if current_user.lte_gb_total is not None
                                else (
                                    active_tariff.lte_gb_total or 0
                                    if active_tariff
                                    else 0
                                )
                            )
                            effective_lte_used = (
                                active_tariff.lte_gb_used if active_tariff else 0
                            )
                            if (effective_lte_total or 0) > (effective_lte_used or 0):
                                lte_allowed = True
                        if lte_allowed:
                            internal_squads.append(lte_uuid)
                    active_internal_squads = internal_squads or None

                    is_web_user = int(self.id) >= WEB_USER_ID_FLOOR
                    base_username = remnawave_username_for_user_id(
                        int(self.id), test=test_mode
                    )
                    legacy_base_username = legacy_remnawave_username_for_user_id(
                        int(self.id), test=test_mode
                    )
                    remnawave_telegram_id = None if is_web_user else self.id
                    remnawave_description = (
                        f"Web: {current_user.email or current_user.name()}"
                        if is_web_user
                        else f"Telegram: {current_user.name()}"
                    )
                    try:
                        response = await remnawave.users.create_user(
                            username=base_username,
                            expire_at=expire_at_date,
                            telegram_id=remnawave_telegram_id,
                            email=current_user.email,
                            description=remnawave_description,
                            hwid_device_limit=normalized_hwid_limit,
                            active_internal_squads=active_internal_squads,
                            external_squad_uuid=remnawave_settings.default_external_squad_uuid,
                        )
                    except Exception as create_err:
                        if self._is_remnawave_username_exists_error(str(create_err)):
                            existing_remote_user = (
                                await self._find_existing_remnawave_user(
                                    remnawave,
                                    base_username=base_username,
                                    legacy_usernames=[legacy_base_username],
                                )
                            )
                            if existing_remote_user and existing_remote_user.get(
                                "uuid"
                            ):
                                current_user.remnawave_uuid = existing_remote_user[
                                    "uuid"
                                ]
                                has_existing_hwid = (
                                    await self._remote_user_has_hwid_device(
                                        remnawave,
                                        current_user.remnawave_uuid,
                                    )
                                )
                                update_fields = [
                                    "remnawave_uuid",
                                    "is_trial",
                                    "used_trial",
                                    "expired_at",
                                    "hwid_limit",
                                ]
                                if hasattr(current_user, "trial_started_at"):
                                    update_fields.append("trial_started_at")
                                update_fields.extend(
                                    self._apply_existing_remnawave_connection_state(
                                        current_user,
                                        existing_remote_user,
                                        has_hwid_device=has_existing_hwid,
                                    )
                                )
                                await current_user.save(
                                    update_fields=list(dict.fromkeys(update_fields))
                                )
                                self.remnawave_uuid = current_user.remnawave_uuid
                                self.hwid_limit = current_user.hwid_limit
                                try:
                                    await remnawave.users.update_user(
                                        current_user.remnawave_uuid,
                                        hwidDeviceLimit=normalized_hwid_limit,
                                        expireAt=expire_at_date,
                                    )
                                except Exception as sync_err:
                                    logger.warning(
                                        "Rebind sync failed for user=%s uuid=%s: %s",
                                        self.id,
                                        self.remnawave_uuid,
                                        sync_err,
                                    )
                                logger.warning(
                                    "Вместо создания дубля выполнен rebind local user=%s к existing RemnaWave uuid=%s",
                                    self.id,
                                    self.remnawave_uuid,
                                )
                                return True
                        raise

                    current_user.remnawave_uuid = (response.get("response") or {}).get(
                        "uuid"
                    )
                    if not current_user.remnawave_uuid:
                        raise ValueError(
                            f"create_user не вернул uuid для пользователя {self.id}"
                        )

                    await current_user.save(
                        update_fields=[
                            "remnawave_uuid",
                            "is_trial",
                            "used_trial",
                            "expired_at",
                            "hwid_limit",
                        ]
                        + (
                            ["trial_started_at"]
                            if hasattr(current_user, "trial_started_at")
                            else []
                        )
                    )
                    self.remnawave_uuid = current_user.remnawave_uuid
                    self.hwid_limit = current_user.hwid_limit
                    logger.info(
                        f"Пользователь {self.id} успешно создан в RemnaWave с UUID: {self.remnawave_uuid}"
                    )
                    return True
                finally:
                    await remnawave.close()
                    logger.debug(
                        "Закрыта сессия клиента RemnaWave в _ensure_remnawave_user для пользователя %s",
                        self.id,
                    )
            except Exception as e:
                logger.error(
                    f"Ошибка при создании пользователя {self.id} в RemnaWave: {str(e)}"
                )
                return False

    async def recreate_remnawave_user(self) -> bool:
        """
        Форсирует пересоздание пользователя в RemnaWave даже если `remnawave_uuid` уже установлен.
        Возвращает True при успешном rebind/create и сохранении UUID.
        """
        lock = await _get_remnawave_user_lock(int(self.id))
        async with lock:
            try:
                from bloobcat.routes.remnawave.client import RemnaWaveClient
                from bloobcat.settings import remnawave_settings
                from datetime import date

                current_user = await Users.get_or_none(id=self.id) or self
                normalized_hwid_limit = self._normalize_hwid_limit_value(
                    current_user.hwid_limit
                )
                hwid_limit_changed = current_user.hwid_limit != normalized_hwid_limit
                if hwid_limit_changed:
                    current_user.hwid_limit = normalized_hwid_limit
                self.remnawave_uuid = current_user.remnawave_uuid
                self.hwid_limit = normalized_hwid_limit

                remnawave = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value(),
                )
                try:
                    if current_user.remnawave_uuid:
                        try:
                            payload = await remnawave.users.get_user_by_uuid(
                                str(current_user.remnawave_uuid)
                            )
                            if self._extract_remnawave_user(payload):
                                if hwid_limit_changed:
                                    await current_user.save(
                                        update_fields=["hwid_limit"]
                                    )
                                self.remnawave_uuid = current_user.remnawave_uuid
                                return True
                        except Exception as lookup_err:
                            if not self._is_remnawave_not_found_error(str(lookup_err)):
                                raise

                    is_web_user = int(self.id) >= WEB_USER_ID_FLOOR
                    base_username = remnawave_username_for_user_id(
                        int(self.id), test=test_mode
                    )
                    legacy_base_username = legacy_remnawave_username_for_user_id(
                        int(self.id), test=test_mode
                    )
                    existing_remote_user = await self._find_existing_remnawave_user(
                        remnawave,
                        base_username=base_username,
                        legacy_usernames=[legacy_base_username],
                    )
                    if existing_remote_user and existing_remote_user.get("uuid"):
                        current_user.remnawave_uuid = existing_remote_user["uuid"]
                        has_existing_hwid = await self._remote_user_has_hwid_device(
                            remnawave,
                            current_user.remnawave_uuid,
                        )
                        update_fields = ["remnawave_uuid", "hwid_limit"]
                        update_fields.extend(
                            self._apply_existing_remnawave_connection_state(
                                current_user,
                                existing_remote_user,
                                has_hwid_device=has_existing_hwid,
                            )
                        )
                        await current_user.save(
                            update_fields=list(dict.fromkeys(update_fields))
                        )
                        self.remnawave_uuid = current_user.remnawave_uuid
                        self.hwid_limit = normalized_hwid_limit
                        try:
                            await remnawave.users.update_user(
                                current_user.remnawave_uuid,
                                hwidDeviceLimit=normalized_hwid_limit,
                                expireAt=(current_user.expired_at or date.today()),
                            )
                        except Exception as sync_err:
                            logger.warning(
                                "Rebind sync failed in recreate_remnawave_user for user=%s uuid=%s: %s",
                                self.id,
                                self.remnawave_uuid,
                                sync_err,
                            )
                        logger.info(
                            "Пользователь %s rebind к существующему RemnaWave UUID: %s",
                            self.id,
                            self.remnawave_uuid,
                        )
                        return True

                    expire_at_date = current_user.expired_at or date.today()
                    hwid_limit = normalized_hwid_limit

                    internal_squads = []
                    if remnawave_settings.default_internal_squad_uuid:
                        internal_squads.append(
                            remnawave_settings.default_internal_squad_uuid
                        )
                    lte_uuid = remnawave_settings.lte_internal_squad_uuid
                    if lte_uuid:
                        lte_allowed = False
                        if current_user.is_trial:
                            lte_allowed = (await read_trial_lte_limit_gb()) > 0
                        elif current_user.active_tariff_id:
                            active_tariff = await ActiveTariffs.get_or_none(
                                id=current_user.active_tariff_id
                            )
                            effective_lte_total = (
                                current_user.lte_gb_total
                                if current_user.lte_gb_total is not None
                                else (
                                    active_tariff.lte_gb_total or 0
                                    if active_tariff
                                    else 0
                                )
                            )
                            effective_lte_used = (
                                active_tariff.lte_gb_used if active_tariff else 0
                            )
                            if (effective_lte_total or 0) > (effective_lte_used or 0):
                                lte_allowed = True
                        if lte_allowed:
                            internal_squads.append(lte_uuid)
                    active_internal_squads = internal_squads or None

                    try:
                        response = await remnawave.users.create_user(
                            username=base_username,
                            expire_at=expire_at_date,
                            telegram_id=None if is_web_user else self.id,
                            email=current_user.email,
                            description=(
                                f"Web: {current_user.email or current_user.name()}"
                                if is_web_user
                                else f"Telegram: {current_user.name()}"
                            ),
                            hwid_device_limit=hwid_limit,
                            active_internal_squads=active_internal_squads,
                            external_squad_uuid=remnawave_settings.default_external_squad_uuid,
                        )
                    except Exception as create_err:
                        if self._is_remnawave_username_exists_error(str(create_err)):
                            existing_remote_user = (
                                await self._find_existing_remnawave_user(
                                    remnawave,
                                    base_username=base_username,
                                    legacy_usernames=[legacy_base_username],
                                )
                            )
                            if existing_remote_user and existing_remote_user.get(
                                "uuid"
                            ):
                                current_user.remnawave_uuid = existing_remote_user[
                                    "uuid"
                                ]
                                await current_user.save(
                                    update_fields=["remnawave_uuid", "hwid_limit"]
                                )
                                self.remnawave_uuid = current_user.remnawave_uuid
                                self.hwid_limit = normalized_hwid_limit
                                try:
                                    await remnawave.users.update_user(
                                        current_user.remnawave_uuid,
                                        hwidDeviceLimit=normalized_hwid_limit,
                                        expireAt=expire_at_date,
                                    )
                                except Exception as sync_err:
                                    logger.warning(
                                        "Rebind sync failed in recreate_remnawave_user collision flow for user=%s uuid=%s: %s",
                                        self.id,
                                        self.remnawave_uuid,
                                        sync_err,
                                    )
                                logger.info(
                                    "Пользователь %s rebind после коллизии username, RemnaWave UUID: %s",
                                    self.id,
                                    self.remnawave_uuid,
                                )
                                return True
                        raise

                    current_user.remnawave_uuid = (response.get("response") or {}).get(
                        "uuid"
                    )
                    if not current_user.remnawave_uuid:
                        raise ValueError(
                            f"create_user не вернул uuid для пользователя {self.id}"
                        )

                    await current_user.save(
                        update_fields=["remnawave_uuid", "hwid_limit"]
                    )
                    self.remnawave_uuid = current_user.remnawave_uuid
                    self.hwid_limit = normalized_hwid_limit
                    logger.info(
                        f"Пользователь {self.id} пересоздан в RemnaWave с UUID: {self.remnawave_uuid}"
                    )
                    return True
                finally:
                    await remnawave.close()
            except Exception as e:
                logger.error(
                    f"Не удалось пересоздать пользователя {self.id} в RemnaWave: {e}"
                )
                return False

    @classmethod
    async def _scrub_stale_subscription_freezes(
        cls,
        user_id: int,
        *,
        created_before: datetime | None = None,
        allow_full_fallback: bool = False,
    ) -> None:
        """Best-effort cleanup for stale freeze artifacts after hard-delete + recreate."""
        try:
            from bloobcat.db.subscription_freezes import SubscriptionFreezes

            scrub_qs = SubscriptionFreezes.filter(user_id=user_id)
            deleted_rows = 0
            used_fallback = False
            if created_before is not None:
                deleted_rows = await scrub_qs.filter(created_at__lt=created_before).delete()
                # SQLite timestamps may collapse rapid delete/recreate flows into the same
                # second, so stale rows can survive the strict boundary filter. On a brand-new
                # local user row there should be no legitimate freezes yet, so fall back to a
                # full scrub for this user id.
                if deleted_rows == 0 and allow_full_fallback:
                    deleted_rows = await scrub_qs.delete()
                    used_fallback = deleted_rows > 0
            else:
                deleted_rows = await scrub_qs.delete()

            if deleted_rows:
                logger.info(
                    "Scrubbed stale subscription_freezes for recreated user=%s: deleted=%s boundary=%s fallback_full_scrub=%s",
                    user_id,
                    deleted_rows,
                    created_before,
                    used_fallback,
                )
        except Exception as cleanup_exc:
            # Non-critical side effect: auth flow should not fail on transient cleanup issues.
            logger.warning(
                "Failed to scrub stale subscription_freezes for recreated user=%s boundary=%s: %s",
                user_id,
                created_before,
                cleanup_exc,
            )

    @classmethod
    def _schedule_remnawave_ensure(cls, user_id: int) -> None:
        async def _runner() -> None:
            try:
                scheduled_user = await cls.get_or_none(id=user_id)
                if not scheduled_user:
                    logger.warning(
                        "Background RemnaWave ensure skipped: user=%s not found",
                        user_id,
                    )
                    return
                if scheduled_user.remnawave_uuid:
                    return
                await scheduled_user._ensure_remnawave_user()
            except Exception as bg_exc:
                logger.warning(
                    "Background RemnaWave ensure failed for user=%s: %s",
                    user_id,
                    bg_exc,
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "Background RemnaWave ensure not scheduled: no running loop for user=%s",
                user_id,
            )
            return

        loop.create_task(_runner())

    @classmethod
    async def get_user(
        cls,
        telegram_user: WebAppUser | User | None,
        referred_by: Optional[int] = None,
        utm: Optional[str] = None,
        ensure_remnawave: bool = True,
    ):
        from bloobcat.funcs.referral_attribution import (
            is_partner_source_utm,
            pick_attribution_utm,
        )

        if not telegram_user:
            logger.error("get_user: telegram_user is None")
            return None, False

        try:
            user, is_new = await Users.update_or_create(
                id=telegram_user.id,
                defaults=dict(
                    username=telegram_user.username,
                    full_name=telegram_user.first_name
                    + (
                        f" {telegram_user.last_name}" if telegram_user.last_name else ""
                    ),
                    familyurl=get_family_url(telegram_user.id),
                ),
            )

            # Логируем, является ли пользователь новым
            logger.debug(
                f"Пользователь {user.id} - новый: {is_new}, текущий UTM: {user.utm}"
            )

            updated_fields: set[str] = set()

            current_utm = (getattr(user, "utm", None) or "").strip()
            if (
                is_new
                and getattr(app_settings, "device_per_user_enabled_for_new_users", False)
                and user.device_per_user_enabled is None
            ):
                user.device_per_user_enabled = True
                updated_fields.add("device_per_user_enabled")

            # Обработка реферала:
            # Важно: пользователь мог уже существовать (например, ранее нажал /start без реф-ссылки),
            # а затем пришёл по реферальной ссылке. В этом случае `referred_by` нужно зафиксировать,
            # но только один раз и только до полноценной регистрации (is_registered=False), чтобы
            # исключить возможность "перепривязки" и абуза.
            referrer = None
            can_set_referrer = (
                bool(referred_by)
                and int(referred_by) != int(user.id)
                and not getattr(user, "referred_by", None)
            )
            should_force_partner_source = bool(
                can_set_referrer and is_partner_source_utm(utm)
            )
            next_utm = pick_attribution_utm(
                current_utm,
                utm,
                force_partner_source=should_force_partner_source,
            )
            if next_utm != (current_utm or None):
                user.utm = next_utm
                updated_fields.add("utm")
                logger.debug(
                    "UTM установлен/обновлён (is_new=%s, force_partner_source=%s): %s",
                    is_new,
                    should_force_partner_source,
                    next_utm,
                )
            elif utm:
                logger.debug(
                    "Пользователь %s сохраняет текущий UTM (incoming=%s, current=%s)",
                    user.id,
                    utm,
                    current_utm,
                )
            if can_set_referrer:
                if referred_by is None:
                    logger.debug(
                        "Referral bind skipped: referred_by unexpectedly missing for user=%s",
                        user.id,
                    )
                else:
                    referred_by_value = int(referred_by)
                    referrer = await cls.get_or_none(id=referred_by)
                    if referrer:
                        rows_updated = await cls.filter(
                            id=user.id,
                            referred_by__isnull=True,
                            is_registered=False,
                        ).update(referred_by=referred_by_value)
                        if rows_updated:
                            user.referred_by = referred_by_value
                            logger.info(
                                "Referral bind: user=%s referred_by=%s (is_new=%s)",
                                user.id,
                                referred_by,
                                is_new,
                            )
                        else:
                            logger.debug(
                                "Referral bind skipped by atomic guard: user=%s provided_referred_by=%s",
                                user.id,
                                referred_by,
                            )
                    else:
                        logger.info(
                            "Referral bind skipped: referrer not found (user=%s referred_by=%s is_new=%s)",
                            user.id,
                            referred_by,
                            is_new,
                        )
            elif referred_by:
                # Extra visibility for debugging referrals that "didn't stick".
                logger.debug(
                    "Referral bind skipped: user=%s provided_referred_by=%s current_referred_by=%s is_registered=%s is_new=%s",
                    user.id,
                    referred_by,
                    int(getattr(user, "referred_by", 0) or 0),
                    bool(getattr(user, "is_registered", False)),
                    is_new,
                )

            if is_new:
                await cls._scrub_stale_subscription_freezes(
                    int(user.id),
                    created_before=getattr(user, "created_at", None),
                    allow_full_fallback=True,
                )

            if is_new:
                # Отправляем уведомление о новом пользователе
                try:
                    delivered = await on_activated_bot(
                        user.id,
                        user.full_name,
                        referrer_id=referrer.id if referrer else None,
                        referrer_name=referrer.full_name if referrer else None,
                        utm=user.utm,
                    )
                    if not delivered:
                        logger.warning(
                            "Лог о новой регистрации не доставлен в админ-чат: user=%s",
                            user.id,
                        )
                except Exception as e:
                    logger.error(
                        f"Ошибка отправки уведомления админу о новом пользователе: {str(e)}"
                    )

            # Сохраняем пользователя, если были изменения (UTM или реферал)
            if updated_fields:
                await user.save(update_fields=sorted(updated_fields))
                logger.debug(
                    f"Пользователь {user.id} сохранен после изменений. Текущий UTM: {user.utm}"
                )
            else:
                logger.debug(
                    f"Пользователь {user.id} не сохранялся, изменений не было. Текущий UTM: {user.utm}"
                )

            # Referral count recalculation is non-critical for auth flow.
            # Do not fail user registration if this side effect is temporary unavailable.
            try:
                await user.count_referrals()
            except Exception as e_referrals:
                logger.warning(
                    "Не удалось пересчитать referrals для user=%s (non-blocking): %s",
                    user.id,
                    e_referrals,
                )

            # Создаем пользователя в RemnaWave, если он еще не создан
            if is_new or not user.remnawave_uuid:
                if ensure_remnawave:
                    await user._ensure_remnawave_user()
                else:
                    cls._schedule_remnawave_ensure(int(user.id))
            # Schedule referral notifications for new users
            if is_new:
                # Scheduling is a post-registration side effect.
                # Registration must still succeed even if scheduler is temporarily unhealthy.
                try:
                    from bloobcat.scheduler import schedule_user_tasks

                    await schedule_user_tasks(user)
                except Exception as e_schedule:
                    logger.error(
                        "Не удалось запланировать задачи для нового user=%s (non-blocking): %s",
                        user.id,
                        e_schedule,
                        exc_info=True,
                    )

            return user, is_new

        except Exception as e:
            logger.error(f"Ошибка создания/обновления пользователя: {str(e)}")
            raise

    def expires(self) -> Optional[int]:
        # Если expired_at не установлен, возвращаем None
        if not self.expired_at:
            return None
        expired_at = normalize_date(self.expired_at)
        days_left = (expired_at - date.today()).days
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
        # Используем normalize_date для безопасного сравнения datetime/date
        expired_at_normalized = normalize_date(self.expired_at)
        start_date = (
            max(expired_at_normalized, current_date)
            if expired_at_normalized
            else current_date
        )

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
        """Обновляет счётчик рефералов атомарно, не трогая другие поля"""
        referrals = await Users.filter(referred_by=self.id, is_registered=True).count()

        # Атомарное обновление только поля referrals (не перезаписывает expired_at и др.)
        await Users.filter(id=self.id).update(referrals=referrals)

        # Синхронизируем in-memory значение
        self.referrals = referrals

    async def get_prize_wheel_attempts(self) -> int:
        """Возвращает количество попыток пользователя на колесе призов"""
        return int(self.prize_wheel_attempts or 0)

    async def delete(self, *args, **kwargs):
        """Удаляет пользователя: отзывает задачи Celery, удаляет в RemnaWave и из локальной БД"""
        # Cancel all scheduled asyncio tasks for this user
        from bloobcat.scheduler import cancel_user_tasks

        cancel_user_tasks(self.id)
        # Safety net: перед удалением пользователя гарантируем CASCADE для active_tariffs.user_id.
        await ensure_active_tariffs_fk_cascade()
        await ensure_notification_marks_fk_cascade()
        await ensure_promo_usages_fk_cascade()
        await ensure_users_referred_by_fk_set_null()
        try:
            from bloobcat.services.device_service import cascade_delete

            await cascade_delete(self)
        except Exception as exc:
            logger.warning(
                "Device-per-user cascade delete failed for user=%s: %s",
                self.id,
                exc,
            )
        # Удаляем пользователя в RemnaWave
        if self.remnawave_uuid:
            from bloobcat.routes.remnawave.client import RemnaWaveClient
            from bloobcat.services.admin_integration import (
                enqueue_remnawave_delete_retry,
                is_remnawave_transient_error,
            )
            from bloobcat.settings import remnawave_settings

            client = RemnaWaveClient(
                remnawave_settings.url, remnawave_settings.token.get_secret_value()
            )
            try:
                await client.users.delete_user(self.remnawave_uuid)
                logger.info(f"Пользователь {self.id} удален из RemnaWave")
            except Exception as e:
                error_text = str(e)
                if self._is_remnawave_not_found_error(error_text):
                    logger.info(
                        "RemnaWave user already missing during delete: user=%s uuid=%s",
                        self.id,
                        self.remnawave_uuid,
                    )
                elif is_remnawave_transient_error(error_text):
                    await enqueue_remnawave_delete_retry(
                        user_id=int(self.id),
                        remnawave_uuid=str(self.remnawave_uuid),
                        last_error=error_text,
                    )
                    logger.warning(
                        "Queued delete retry after transient RemnaWave error: user=%s uuid=%s error=%s",
                        self.id,
                        self.remnawave_uuid,
                        error_text,
                    )
                else:
                    logger.error(
                        "Non-retryable RemnaWave delete error for user=%s uuid=%s: %s",
                        self.id,
                        self.remnawave_uuid,
                        error_text,
                    )
            finally:
                await client.close()
        # Выполняем удаление из БД через оригинальный delete
        result = await super().delete(*args, **kwargs)
        logger.info(f"Пользователь {self.id} удален из локальной базе данных")
        return result

    async def save(self, *args, **kwargs):
        skip_remnawave_sync = bool(kwargs.pop("skip_remnawave_sync", False))

        # Check previous values for fields that affect task scheduling
        old_expired_at = None
        old_is_subscribed = None
        old_is_trial = None
        old_is_blocked = None
        old_hwid_limit = None
        old_device_per_user_enabled = None
        had_existing_row = False
        should_reschedule = False

        if self.id is not None:
            orig = await Users.get_or_none(id=self.id)
            if orig:
                had_existing_row = True
                old_expired_at = normalize_date(orig.expired_at)
                old_is_subscribed = orig.is_subscribed
                old_is_trial = orig.is_trial
                old_is_blocked = orig.is_blocked
                old_hwid_limit = orig.hwid_limit
                old_device_per_user_enabled = orig.is_device_per_user_enabled()

                # Check if any fields that affect scheduling have changed
                current_expired_at = normalize_date(self.expired_at)
                should_reschedule = (
                    current_expired_at != old_expired_at
                    or self.is_subscribed != old_is_subscribed
                    or self.is_trial != old_is_trial
                    or self.is_blocked != old_is_blocked
                )
            else:
                # New user, always schedule tasks
                should_reschedule = True
        else:
            # New user, always schedule tasks
            should_reschedule = True

        # Save the user as usual
        await super().save(*args, **kwargs)

        device_per_user_enabled_changed = (
            had_existing_row
            and old_device_per_user_enabled is not None
            and self.is_device_per_user_enabled() != old_device_per_user_enabled
        )

        if (
            not skip_remnawave_sync
            and self.expired_at
            and normalize_date(self.expired_at) != old_expired_at
        ):
            # Немедленно обновляем RemnaWave при изменении expired_at
            if self.remnawave_uuid:
                try:
                    from bloobcat.routes.remnawave.client import RemnaWaveClient
                    from bloobcat.settings import remnawave_settings

                    remnawave_client = RemnaWaveClient(
                        remnawave_settings.url,
                        remnawave_settings.token.get_secret_value(),
                    )

                    try:
                        await remnawave_client.users.update_user(
                            uuid=self.remnawave_uuid, expireAt=self.expired_at
                        )
                        logger.debug(
                            f"User {self.id} RemnaWave updated immediately: {old_expired_at} -> {self.expired_at}"
                        )
                    finally:
                        await remnawave_client.close()

                except Exception as e:
                    # Если пользователь был удален в RemnaWave – пересоздаем и пытаемся обновить снова
                    err_text = str(e)
                    if self._is_remnawave_not_found_error(err_text) or any(
                        token in err_text for token in ["A039", "Update user error"]
                    ):
                        recreated = await self.recreate_remnawave_user()
                        if recreated and self.remnawave_uuid:
                            try:
                                from bloobcat.routes.remnawave.client import (
                                    RemnaWaveClient,
                                )
                                from bloobcat.settings import remnawave_settings

                                remnawave_client = RemnaWaveClient(
                                    remnawave_settings.url,
                                    remnawave_settings.token.get_secret_value(),
                                )
                                try:
                                    await remnawave_client.users.update_user(
                                        uuid=self.remnawave_uuid,
                                        expireAt=self.expired_at,
                                    )
                                    logger.info(
                                        f"User {self.id} RemnaWave re-created and updated immediately"
                                    )
                                finally:
                                    await remnawave_client.close()
                            except Exception as e2:
                                logger.warning(
                                    f"User {self.id} re-create update attempt failed: {e2}"
                                )
                    else:
                        logger.warning(
                            f"User {self.id} failed to update RemnaWave immediately, will be synced by batch updater: {e}"
                        )
        if (
            not skip_remnawave_sync
            and had_existing_row
            and self.is_device_per_user_enabled()
            and (
                normalize_date(self.expired_at) != old_expired_at
                or self.hwid_limit != old_hwid_limit
                or device_per_user_enabled_changed
            )
        ):
            try:
                from bloobcat.services.device_service import sync_device_entitlements

                await sync_device_entitlements(self)
            except Exception as exc:
                logger.warning(
                    "Device-per-user entitlement sync failed for user=%s: %s",
                    self.id,
                    exc,
                )
        if (
            not skip_remnawave_sync
            and had_existing_row
            and device_per_user_enabled_changed
            and not self.is_device_per_user_enabled()
            and self.remnawave_uuid
            and self.hwid_limit is not None
        ):
            try:
                from bloobcat.routes.remnawave.client import RemnaWaveClient
                from bloobcat.settings import remnawave_settings

                remnawave_client = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value(),
                )
                try:
                    await remnawave_client.users.update_user(
                        uuid=self.remnawave_uuid,
                        hwidDeviceLimit=max(0, int(self.hwid_limit or 0)),
                    )
                finally:
                    await remnawave_client.close()
            except Exception as exc:
                logger.warning(
                    "Device-per-user disable legacy limit restore failed for user=%s: %s",
                    self.id,
                    exc,
                )

        # Перепланируем задачи только при изменении важных полей
        if should_reschedule:
            try:
                from bloobcat.scheduler import schedule_user_tasks

                logger.debug(
                    f"Rescheduling tasks for user {self.id} after save (fields changed)."
                )
                await schedule_user_tasks(self)
            except Exception as e:
                logger.error(f"Failed to reschedule tasks for user {self.id}: {e}")
        else:
            logger.debug(
                f"User {self.id} saved without rescheduling tasks (no relevant changes)."
            )

    class PydanticMeta:
        computed = ["expires", "name", "referral_percent"]
        exclude = [
            "country_code",
            "temp_setup_token",
            "temp_setup_expires_at",
            "temp_setup_device_id",
        ]

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
                is_blocked=True, blocked_at__gte=last_24h
            ).count()

            # Пользователи готовые к очистке
            cutoff_date = datetime.now(MOSCOW) - timedelta(
                days=app_settings.blocked_user_cleanup_days
            )
            today = datetime.now(MOSCOW).date()
            subscription_cutoff_date = today - timedelta(
                days=app_settings.blocked_user_cleanup_days
            )

            # СЛУЧАЙ 1: Триальные пользователи (заблокированы > 7 дней назад)
            blocked_trial_ready = await Users.filter(
                is_blocked=True, blocked_at__lte=cutoff_date, is_trial=True
            ).count()

            # СЛУЧАЙ 2: Платные пользователи с истекшей подпиской (заблокированы > 7 дней И подписка истекла > 7 дней назад)
            blocked_paid_expired_ready = await Users.filter(
                is_blocked=True,
                blocked_at__lte=cutoff_date,
                is_trial=False,
                expired_at__lte=subscription_cutoff_date,
            ).count()

            ready_for_cleanup = blocked_trial_ready + blocked_paid_expired_ready

            # Платные пользователи с активной подпиской (которые НЕ удаляются)
            blocked_paid_active = await Users.filter(
                is_blocked=True,
                blocked_at__lte=cutoff_date,
                is_trial=False,
                expired_at__gt=subscription_cutoff_date,
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
                "max_failed_attempts": app_settings.blocked_user_max_failed_attempts,
            }
        except Exception as e:
            logger.error(f"Error getting blocked users stats: {e}")
            return {"error": str(e)}


User_Pydantic = pydantic_model_creator(Users, name="User")


class UsersUpdateSchema(FastAdminBaseModel):
    """Схема обновления пользователя через админку (включая LTE лимит)."""

    lte_gb_total: Optional[int] = Field(
        None, description="LTE лимит (GB) для активного тарифа"
    )


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
        "device_per_user_enabled",
        "is_admin",
        "is_partner",
        "is_blocked",
        "blocked_at",
        "prize_wheel_attempts",
    )
    list_editable = ("prize_wheel_attempts",)
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
        "remnawave_uuid",
        "familyurl",
        "active_tariff",
        "lte_gb_total",
        "hwid_limit",
        "device_per_user_enabled",
        "is_blocked",
        "blocked_at",
        "last_failed_message_at",
        "failed_message_count",
        "prize_wheel_attempts",
    )
    search_help_text = "Юзернейм, имя, айди"
    verbose_name = "Пользователи"
    verbose_name_plural = "Пользователи"
    update_schema = UsersUpdateSchema

    async def save_model(self, pk, form_data=None):
        """
        Переопределенный метод сохранения пользователя.

        После сохранения в БД обновляет дату истечения и лимит устройств в RemnaWave.

        :param pk: Первичный ключ пользователя (ID)
        :param form_data: Данные формы с изменениями
        """
        if form_data is None:
            form_data = {}
        lte_gb_total = form_data.get("lte_gb_total", None)

        original_obj = None
        original_expired_at = None
        original_hwid_limit = None
        original_device_per_user_enabled = None

        if pk:
            original_obj = await Users.get_or_none(id=pk)
            if original_obj:
                original_expired_at = original_obj.expired_at
                original_hwid_limit = original_obj.hwid_limit
                original_device_per_user_enabled = (
                    original_obj.is_device_per_user_enabled()
                )

        pending_is_trial = form_data.get(
            "is_trial", original_obj.is_trial if original_obj else False
        )
        if "active_tariff" in form_data:
            pending_active_tariff = form_data.get("active_tariff")
            if pending_active_tariff is None:
                pending_active_tariff_id = None
            else:
                pending_active_tariff_id = getattr(
                    pending_active_tariff, "id", pending_active_tariff
                )
        else:
            pending_active_tariff_id = (
                original_obj.active_tariff_id if original_obj else None
            )
        if pending_is_trial and pending_active_tariff_id:
            raise ValueError("Нельзя включать is_trial при активном тарифе.")

        result = await super().save_model(pk, form_data)

        obj = await Users.get(id=pk)

        if lte_gb_total is not None:
            try:
                lte_gb_total = int(lte_gb_total)
            except (TypeError, ValueError):
                lte_gb_total = None

        if lte_gb_total is not None:
            if lte_gb_total < 0:
                lte_gb_total = 0
            active_tariff = None
            if obj.active_tariff_id:
                active_tariff = await ActiveTariffs.get_or_none(id=obj.active_tariff_id)
            if active_tariff:
                active_tariff.lte_gb_total = lte_gb_total
                await active_tariff.save(update_fields=["lte_gb_total"])
                try:
                    from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
                    from bloobcat.db.notifications import NotificationMarks

                    should_enable = lte_gb_total > float(active_tariff.lte_gb_used or 0)
                    if obj.remnawave_uuid:
                        await set_lte_squad_status(
                            str(obj.remnawave_uuid), enable=should_enable
                        )
                    await NotificationMarks.filter(
                        user_id=obj.id, type="lte_usage"
                    ).delete()
                except Exception as e:
                    logger.error(f"Ошибка обновления LTE лимита для {obj.id}: {e}")
            else:
                try:
                    from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
                    from bloobcat.db.notifications import NotificationMarks

                    # Для пользователей без active_tariff_id (триал/партнеры) сверяем
                    # фактический расход в RemnaWave, чтобы не включать LTE при нулевом остатке.
                    should_enable = lte_gb_total > 0
                    if obj.remnawave_uuid:
                        from datetime import (
                            datetime,
                            timezone,
                            timedelta,
                            time as dt_time,
                        )
                        from bloobcat.routes.remnawave.client import RemnaWaveClient
                        from bloobcat.settings import remnawave_settings

                        BYTES_IN_GB = 1024**3
                        MSK_TZ = timezone(timedelta(hours=3))
                        marker_upper = (
                            remnawave_settings.lte_node_marker or ""
                        ).upper()

                        created_at = obj.created_at
                        if created_at:
                            if getattr(created_at, "tzinfo", None):
                                start_date = created_at.astimezone(MSK_TZ).date()
                            else:
                                created_at_utc = created_at.replace(tzinfo=timezone.utc)
                                start_date = created_at_utc.astimezone(MSK_TZ).date()
                        else:
                            start_date = datetime.now(MSK_TZ).date()

                        start_dt = datetime.combine(
                            start_date, dt_time.min, tzinfo=MSK_TZ
                        )
                        start_str = start_dt.astimezone(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        )
                        end_str = datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        )

                        client = RemnaWaveClient(
                            remnawave_settings.url,
                            remnawave_settings.token.get_secret_value(),
                        )
                        try:
                            resp = await client.users.get_user_usage_by_range(
                                str(obj.remnawave_uuid),
                                start_str,
                                end_str,
                            )
                            items = resp.get("response") or []
                            used_gb = 0.0
                            for item in items:
                                node_name = str(item.get("nodeName") or "").upper()
                                if marker_upper and marker_upper not in node_name:
                                    continue
                                total_bytes = float(item.get("total") or 0)
                                used_gb += total_bytes / BYTES_IN_GB
                            should_enable = float(lte_gb_total) > used_gb
                        finally:
                            await client.close()

                        await set_lte_squad_status(
                            str(obj.remnawave_uuid), enable=should_enable
                        )

                    await NotificationMarks.filter(
                        user_id=obj.id, type="lte_usage"
                    ).delete()
                    logger.info(
                        "Admin LTE update without active_tariff: user=%s total=%s enable=%s",
                        obj.id,
                        lte_gb_total,
                        should_enable,
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка обновления LTE лимита для {obj.id} (без active_tariff): {e}"
                    )

        logger.debug(
            f"Пользователь {obj.id} сохранен. Проверяем необходимость обновления в RemnaWave"
        )

        if obj.remnawave_uuid:
            updates_needed = {}
            device_per_user_sync_needed = False
            device_per_user_enabled_changed = (
                original_device_per_user_enabled is not None
                and obj.is_device_per_user_enabled()
                != original_device_per_user_enabled
            )
            if device_per_user_enabled_changed:
                if obj.is_device_per_user_enabled():
                    device_per_user_sync_needed = True
                elif obj.hwid_limit is not None:
                    updates_needed["hwidDeviceLimit"] = obj.hwid_limit

            # Проверяем изменение даты истечения
            obj_expired_at = normalize_date(obj.expired_at)
            orig_expired_at = normalize_date(original_expired_at)
            if obj_expired_at and obj_expired_at != orig_expired_at:
                from datetime import date

                today = date.today()

                if obj_expired_at < today:
                    logger.warning(
                        f"Дата истечения {obj.expired_at} в прошлом. Пропускаем обновление даты."
                    )
                else:
                    updates_needed["expireAt"] = obj.expired_at
                    device_per_user_sync_needed = bool(
                        obj.is_device_per_user_enabled()
                    )
                    logger.debug(
                        f"Дата истечения изменилась: {original_expired_at} -> {obj.expired_at}"
                    )

            # Проверяем изменение hwid_limit
            if obj.hwid_limit is not None and obj.hwid_limit != original_hwid_limit:
                if obj.is_device_per_user_enabled():
                    device_per_user_sync_needed = True
                else:
                    updates_needed["hwidDeviceLimit"] = obj.hwid_limit
                logger.debug(
                    f"Лимит устройств изменился: {original_hwid_limit} -> {obj.hwid_limit}"
                )

            # Обновляем RemnaWave, если есть изменения
            if updates_needed:
                try:
                    from bloobcat.routes.remnawave.client import RemnaWaveClient

                    client = RemnaWaveClient(
                        remnawave_settings.url,
                        remnawave_settings.token.get_secret_value(),
                    )
                    try:
                        await client.users.update_user(
                            obj.remnawave_uuid, **updates_needed
                        )
                        logger.debug(
                            f"Данные для пользователя {obj.id} обновлены в RemnaWave: {updates_needed}"
                        )
                    finally:
                        await client.close()
                except Exception as e:
                    logger.error(
                        f"Ошибка при обновлении данных в RemnaWave для пользователя {obj.id}: {e}",
                        exc_info=True,
                    )
            if device_per_user_sync_needed:
                try:
                    from bloobcat.services.device_service import sync_device_entitlements

                    await sync_device_entitlements(obj)
                except Exception as e:
                    logger.error(
                        f"Ошибка синхронизации device-per-user для пользователя {obj.id}: {e}",
                        exc_info=True,
                    )
        else:
            logger.warning(
                f"Пользователь {obj.id} не имеет UUID в RemnaWave, обновления пропускаются"
            )

        return result

    async def delete_model(self, pk: int):
        """Удаление пользователя через админку: используем переопределённый delete() модели"""
        # Получаем пользователя
        user_obj = await Users.get_or_none(id=pk)
        if user_obj:
            # Удаляем через метод delete(), где отзываются задачи Celery и удаляется из RemnaWave
            await user_obj.delete()
            logger.info(
                f"Пользователь {pk} удалён через админку с revocation Celery и RemnaWave"
            )
        else:
            logger.warning(
                f"Пользователь с ID {pk} не найден при удалении через админку"
            )
        # Возвращаем стандартный ответ админки
        await super().delete_model(pk)

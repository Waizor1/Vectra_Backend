from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Iterable, List, Optional, Sequence

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.users import Users, normalize_date
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings

from .schemas import (
    ActiveSubscription,
    CaptainUserProfile,
    RemnaWaveDevice,
    RemnaWaveSnapshot,
)

logger = get_logger("captain_user_lookup.repository")


@dataclass
class _PaymentSnapshot:
    payment_id: str
    amount: float
    processed_at: datetime
    status: str


class CaptainUserLookupRepository:
    """Достаёт реальные данные пользователя из БД."""

    async def get_profile(self, telegram_id: int) -> Optional[CaptainUserProfile]:
        user = (
            await Users.filter(id=telegram_id)
            .select_related("active_tariff")
            .first()
        )
        if not user:
            return None

        tariffs = await ActiveTariffs.filter(user_id=user.id)
        payments = await self._get_recent_payments(user.id)

        first_name, last_name = self._split_name(user.full_name)
        remnawave_snapshot = await self._fetch_remnawave_snapshot(user)

        profile = CaptainUserProfile(
            telegram_id=user.id,
            first_name=first_name,
            last_name=last_name,
            username=user.username or "",
            email=user.email or "",
            phone=None,
            country=self._map_language_to_country(user.language_code),
            status=self._determine_status(user),
            active_subscriptions=self._build_subscriptions(
                user, tariffs, payments
            ),
            balance=float(user.balance or 0),
            registered_at=self._to_datetime(user.registration_date or user.created_at),
            last_login=self._to_datetime(user.connected_at or user.registration_date),
            remnawave=remnawave_snapshot,
        )
        return profile

    async def _get_recent_payments(self, user_id: int) -> List[_PaymentSnapshot]:
        payments = (
            await ProcessedPayments.filter(user_id=user_id, status="succeeded")
            .order_by("-processed_at")
            .limit(5)
        )
        return [
            _PaymentSnapshot(
                payment_id=p.payment_id,
                amount=float(p.amount),
                processed_at=p.processed_at,
                status=p.status,
            )
            for p in payments
        ]

    def _build_subscriptions(
        self,
        user: Users,
        tariffs: Iterable[ActiveTariffs],
        payments: Iterable[_PaymentSnapshot],
    ) -> List[ActiveSubscription]:
        entries: List[ActiveSubscription] = []
        payments_list = list(payments)

        for tariff in tariffs:
            entries.append(
                ActiveSubscription(
                    name=tariff.name,
                    status=self._determine_status(user),
                    months=tariff.months,
                    price=float(tariff.price),
                    started_at=self._guess_subscription_start(user, payments_list),
                    expires_at=self._to_datetime(user.expired_at),
                )
            )

        if not entries and getattr(user, "active_tariff", None):
            active_tariff = user.active_tariff
            entries.append(
                ActiveSubscription(
                    name=active_tariff.name,
                    status=self._determine_status(user),
                    months=active_tariff.months,
                    price=float(active_tariff.price),
                    started_at=self._guess_subscription_start(user, payments_list),
                    expires_at=self._to_datetime(user.expired_at),
                )
            )

        payment_entries = [
            ActiveSubscription(
                name=f"Payment {payment.payment_id}",
                status=payment.status,
                months=None,
                price=payment.amount,
                started_at=payment.processed_at,
                expires_at=None,
            )
            for payment in payments_list
        ]
        entries.extend(payment_entries)

        return entries

    def _guess_subscription_start(
        self, user: Users, payments: Sequence[_PaymentSnapshot]
    ) -> Optional[datetime]:
        if payments:
            return payments[0].processed_at
        if user.connected_at:
            return user.connected_at
        return self._to_datetime(user.registration_date)

    @staticmethod
    def _split_name(full_name: str) -> tuple[str, str]:
        if not full_name:
            return "", ""
        parts = full_name.split(" ", 1)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1]

    @staticmethod
    def _to_datetime(value: datetime | date | None) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.min)
        return None

    @staticmethod
    def _map_language_to_country(language_code: Optional[str]) -> Optional[str]:
        if not language_code:
            return None
        mapping = {
            "ru": "RU",
            "uk": "UA",
            "be": "BY",
            "kk": "KZ",
            "uz": "UZ",
            "en": "US",
            "tr": "TR",
        }
        return mapping.get(language_code.lower(), language_code.upper())

    @staticmethod
    def _determine_status(user: Users) -> str:
        if user.is_blocked:
            return "blocked"
        expired_at = normalize_date(user.expired_at)
        today = date.today()
        if user.is_trial and expired_at:
            return "trial_active" if expired_at >= today else "trial_expired"
        if expired_at:
            return "active" if expired_at >= today else "expired"
        return "new"

    async def _fetch_remnawave_snapshot(self, user: Users) -> Optional[RemnaWaveSnapshot]:
        if not user.remnawave_uuid:
            return None

        client = RemnaWaveClient(
            remnawave_settings.url, remnawave_settings.token.get_secret_value()
        )
        devices: list[RemnaWaveDevice] | None = None
        try:
            response = await client.users.get_user_by_uuid(str(user.remnawave_uuid))
            devices = await self._fetch_hwid_devices(client, str(user.remnawave_uuid))
        except Exception as exc:
            logger.warning(
                "Failed to fetch RemnaWave data for user {}: {}",
                user.id,
                exc,
            )
            return None
        finally:
            await client.close()

        payload = response.get("response") or {}
        # В новой панели happ.cryptoLink может отсутствовать — используем fallback через encrypt tool
        subscription_url = (payload.get("happ") or {}).get("cryptoLink")
        if not subscription_url:
            raw_sub_url = payload.get("subscriptionUrl")
            if raw_sub_url:
                encrypt_client: RemnaWaveClient | None = None
                try:
                    encrypt_client = RemnaWaveClient(
                        remnawave_settings.url,
                        remnawave_settings.token.get_secret_value(),
                    )
                    subscription_url = await encrypt_client.tools.encrypt_happ_crypto_link(
                        raw_sub_url
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to encrypt subscriptionUrl for user {}: {}",
                        user.id,
                        exc,
                    )
                finally:
                    if encrypt_client:
                        try:
                            await encrypt_client.close()
                        except Exception:
                            pass
        active_squads = payload.get("activeInternalSquads")
        if isinstance(active_squads, list):
            active_squads = [str(item) for item in active_squads]
        else:
            active_squads = None

        return RemnaWaveSnapshot(
            uuid=str(payload.get("uuid") or user.remnawave_uuid),
            username=payload.get("username"),
            status=payload.get("status"),
            expire_at=self._parse_iso_datetime(payload.get("expireAt")),
            online_at=self._parse_iso_datetime(payload.get("onlineAt")),
            hwid_limit=payload.get("hwidDeviceLimit"),
            traffic_limit_bytes=payload.get("trafficLimitBytes"),
            subscription_url=subscription_url,
            telegram_id=payload.get("telegramId"),
            email=payload.get("email"),
            active_internal_squads=active_squads,
            devices=devices,
        )

    @staticmethod
    def _parse_iso_datetime(raw_value: Optional[str]) -> Optional[datetime]:
        if not raw_value:
            return None
        try:
            value = raw_value
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            return datetime.fromisoformat(value)
        except Exception:
            return None

    async def _fetch_hwid_devices(
        self, client: RemnaWaveClient, user_uuid: str
    ) -> list[RemnaWaveDevice] | None:
        try:
            raw = await client.users.get_user_hwid_devices(str(user_uuid))
        except Exception as exc:
            logger.warning(
                "Failed to fetch HWID devices for user {}: {}",
                user_uuid,
                exc,
            )
            return None

        devices_payload: list[dict] = []
        if isinstance(raw, list):
            devices_payload = [item for item in raw if isinstance(item, dict)]
        elif isinstance(raw, dict):
            data = raw.get("response", raw)
            if isinstance(data, dict):
                maybe_devices = data.get("devices")
                if isinstance(maybe_devices, list):
                    devices_payload = [item for item in maybe_devices if isinstance(item, dict)]
                elif isinstance(data.get("response"), list):
                    devices_payload = [
                        item for item in data.get("response", []) if isinstance(item, dict)
                    ]
            elif isinstance(data, list):
                devices_payload = [item for item in data if isinstance(item, dict)]

        parsed_devices: list[RemnaWaveDevice] = []
        for item in devices_payload:
            hwid = item.get("hwid")
            if not hwid:
                continue
            parsed_devices.append(
                RemnaWaveDevice(
                    hwid=str(hwid),
                    user_uuid=str(item.get("userUuid")) if item.get("userUuid") else None,
                    platform=item.get("platform"),
                    os_version=item.get("osVersion"),
                    device_model=item.get("deviceModel"),
                    user_agent=item.get("userAgent"),
                    created_at=self._parse_iso_datetime(item.get("createdAt")),
                    updated_at=self._parse_iso_datetime(
                        item.get("updatedAt") or item.get("UpdatedAt")
                    ),
                )
            )

        return parsed_devices or None


user_repository = CaptainUserLookupRepository()

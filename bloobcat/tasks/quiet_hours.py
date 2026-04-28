import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.notifications import NotificationMarks

MOSCOW = ZoneInfo("Europe/Moscow")
MORNING_DELIVERY_TIME = time(8, 0)

SUBSCRIPTION_EXPIRED_MARK_TYPE = "subscription_expired"
SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE = (
    "subscription_cancelled_after_failures"
)
PENDING_TRIAL_ENDED_MARK_TYPE = "pending_trial_ended"
PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE = "pending_subscription_cancelled"
PENDING_WINBACK_DISCOUNT_MARK_TYPE = "pending_winback_discount"


def ensure_moscow_datetime(value: datetime | None = None) -> datetime:
    current = value or datetime.now(MOSCOW)
    if current.tzinfo is None:
        return current.replace(tzinfo=MOSCOW)
    return current.astimezone(MOSCOW)


def is_quiet_hours(value: datetime | None = None) -> bool:
    current = ensure_moscow_datetime(value)
    return current.hour < MORNING_DELIVERY_TIME.hour


def normalize_user_notification_eta(at_time: datetime) -> datetime:
    normalized = ensure_moscow_datetime(at_time)
    if not is_quiet_hours(normalized):
        return normalized
    return datetime.combine(
        normalized.date(),
        MORNING_DELIVERY_TIME,
        tzinfo=MOSCOW,
    )


def is_in_morning_window(value: datetime | None = None, hours: int = 1) -> bool:
    current = ensure_moscow_datetime(value)
    start = datetime.combine(current.date(), MORNING_DELIVERY_TIME, tzinfo=MOSCOW)
    end = start + timedelta(hours=hours)
    return start <= current <= end


def build_pending_meta(**payload: object) -> str | None:
    filtered = {key: value for key, value in payload.items() if value is not None}
    if not filtered:
        return None
    return json.dumps(filtered, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def parse_pending_meta(raw_meta: str | None) -> dict[str, object]:
    if not raw_meta:
        return {}
    try:
        parsed = json.loads(raw_meta)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def build_winback_notification_key(expires_at: date) -> str:
    return f"offer:{expires_at.isoformat()}"


async def ensure_notification_mark(
    *,
    user_id: int,
    mark_type: str,
    key: str | None = None,
    meta: str | None = None,
) -> bool:
    _mark, created = await NotificationMarks.get_or_create(
        user_id=user_id,
        type=mark_type,
        key=key,
        meta=meta,
    )
    return created

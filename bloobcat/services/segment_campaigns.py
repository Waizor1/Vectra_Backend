"""Резолвер сегментов аудитории и выбор активной акции для пользователя.

Сегменты вычисляются по:
- наличию успешных платежей (ProcessedPayments status='succeeded'),
- датам регистрации/триала и истечения подписки.

Это автономный сервис без побочных эффектов: вызывается из роутов
подписки, чтобы фронт получил единый ответ "какая акция применима
к этому пользователю прямо сейчас".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from tortoise.expressions import Q

from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.segment_campaigns import SegmentCampaign
from bloobcat.db.users import Users, normalize_date


LAPSED_GRACE_DAYS = 7
LOYAL_PAID_PAYMENTS_THRESHOLD = 2
LOYAL_TENURE_DAYS_THRESHOLD = 180


#: Назначения платежей, которые НЕ считаются «покупкой подписки»
#: для маркетинговой логики сегментов. Топапы трафика и устройств —
#: это апсейл уже состоявшейся подписки, а не первая покупка, поэтому
#: они не должны выбивать пользователя из сегментов `no_purchase_yet`
#: и `trial_active`.
_NON_SUBSCRIPTION_PAYMENT_PURPOSES = ("lte_topup", "devices_topup")


async def _count_successful_paid_payments(user_id: int) -> int:
    """Считаем ровно «оплаченные деньгами» покупки подписки.

    Фильтры:
    - `amount_external > 0` исключает чисто-бонусные/реферальные списания
      с внутреннего баланса, чтобы такие пользователи всё ещё считались
      «не платившими» для маркетинговой логики.
    - `payment_purpose NOT IN (lte_topup, devices_topup)` исключает
      топапы трафика и устройств — они апсейл подписки, не первая покупка.
      NULL у legacy-записей (до миграции 116) трактуется как
      «subscription» и попадает в счётчик, что сохраняет историческое
      поведение для старых пользователей.
    """

    # Тонкость SQL: `NOT IN (...)` возвращает NULL (=== не-true) для строк,
    # где `payment_purpose IS NULL`, и они исключаются из результата. Нам
    # нужно явно сохранить такие строки — это legacy-записи до миграции 116,
    # которые трактуются как подписка.
    qs = ProcessedPayments.filter(
        user_id=user_id,
        status="succeeded",
        amount_external__gt=0,
    ).filter(
        Q(payment_purpose__isnull=True)
        | ~Q(payment_purpose__in=list(_NON_SUBSCRIPTION_PAYMENT_PURPOSES))
    )
    return await qs.count()


async def _user_subscription_lapsed_days(user: Users) -> Optional[int]:
    expired = normalize_date(user.expired_at)
    if expired is None:
        return None
    today = date.today()
    if expired >= today:
        return 0
    return (today - expired).days


def _user_tenure_days(user: Users) -> int:
    created_at = getattr(user, "created_at", None) or getattr(
        user, "registration_date", None
    )
    if not created_at:
        return 0
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - created_at
        return max(0, int(delta.total_seconds() // 86400))
    return 0


async def resolve_user_segments(user: Users) -> List[str]:
    """Возвращает список сегментов, к которым принадлежит пользователь.

    Один пользователь может принадлежать нескольким сегментам одновременно
    (например, `loyal_renewer` и `lapsed`). Кампании, в свою очередь,
    выбираются по приоритету.
    """

    segments: List[str] = ["everyone"]

    paid_count = await _count_successful_paid_payments(int(user.id))
    has_paid = paid_count > 0
    lapsed_days = await _user_subscription_lapsed_days(user)
    tenure_days = _user_tenure_days(user)

    if not has_paid:
        if bool(getattr(user, "is_trial", False)) and bool(
            getattr(user, "is_subscribed", False)
        ):
            segments.append("trial_active")
        else:
            segments.append("no_purchase_yet")

    if has_paid and lapsed_days is not None and lapsed_days > LAPSED_GRACE_DAYS:
        segments.append("lapsed")

    if has_paid and (
        paid_count >= LOYAL_PAID_PAYMENTS_THRESHOLD
        or tenure_days >= LOYAL_TENURE_DAYS_THRESHOLD
    ):
        segments.append("loyal_renewer")

    return segments


async def select_active_campaign(
    user: Users, *, now: Optional[datetime] = None
) -> Optional[SegmentCampaign]:
    """Возвращает наиболее релевантную живую кампанию для пользователя.

    Алгоритм:
    1. Берём все is_active=True кампании в окне `[starts_at; ends_at]`.
    2. Фильтруем по сегментам пользователя (или `everyone`).
    3. Сортируем по `priority` (desc), затем по `ends_at` (asc — кто ближе
       к финишу, тот «горячее»).
    """

    moment = now or datetime.now(timezone.utc)
    user_segments = set(await resolve_user_segments(user))

    qs = SegmentCampaign.filter(
        is_active=True,
        starts_at__lte=moment,
        ends_at__gt=moment,
    ).order_by("-priority", "ends_at")

    candidates = await qs

    for campaign in candidates:
        if campaign.segment in user_segments:
            return campaign
    return None


async def build_active_campaign_payload(
    user: Users, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Готовый JSON-ответ для фронтенда: сегменты + выбранная кампания.

    Если активной кампании нет — `campaign=None`, фронт скрывает баннер.
    """

    moment = now or datetime.now(timezone.utc)
    segments = await resolve_user_segments(user)
    campaign = await select_active_campaign(user, now=moment)
    return {
        "segments": segments,
        "serverNowMs": int(moment.timestamp() * 1000),
        "campaign": campaign.to_public_dict() if campaign else None,
    }

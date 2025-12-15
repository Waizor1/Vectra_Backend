from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.users import Users


@dataclass(frozen=True)
class UTMStatsBlock:
    total: int
    registered: int
    active_now: int
    paid: int
    paid_amount_external: Decimal


@dataclass(frozen=True)
class UTMStats:
    direct: UTMStatsBlock
    indirect: UTMStatsBlock
    total: UTMStatsBlock


async def _paid_users_count_and_amount_external(user_ids: Iterable[int]) -> tuple[int, Decimal]:
    ids = list(user_ids)
    if not ids:
        return 0, Decimal("0")

    payments = await ProcessedPayments.filter(user_id__in=ids, status="succeeded").values(
        "user_id", "amount_external"
    )
    paid_user_ids = {int(p["user_id"]) for p in payments if Decimal(str(p.get("amount_external") or 0)) > 0}
    total_amount = sum((Decimal(str(p.get("amount_external") or 0)) for p in payments), Decimal("0"))
    return len(paid_user_ids), total_amount


async def _stats_for_user_ids(user_ids: Iterable[int], moscow_today: date) -> UTMStatsBlock:
    ids = list(user_ids)
    if not ids:
        return UTMStatsBlock(
            total=0,
            registered=0,
            active_now=0,
            paid=0,
            paid_amount_external=Decimal("0"),
        )

    total = len(ids)
    registered = await Users.filter(id__in=ids, is_registered=True).count()
    active_now = await Users.filter(
        id__in=ids, is_registered=True, expired_at__gt=moscow_today
    ).count()
    paid, paid_amount_external = await _paid_users_count_and_amount_external(ids)

    return UTMStatsBlock(
        total=total,
        registered=registered,
        active_now=active_now,
        paid=paid,
        paid_amount_external=paid_amount_external,
    )


async def _descendants_user_ids(
    root_user_ids: Iterable[int],
    *,
    max_depth: Optional[int] = None,
) -> set[int]:
    """
    Возвращает всех потомков (рефералов) для заданного набора пользователей.

    Потомок = пользователь, у которого `referred_by` указывает на одного из пользователей
    из множества на предыдущем шаге обхода.

    max_depth:
      - None: обход до конца (все уровни)
      - 1: только 1 уровень (прямые рефералы)
      - 2: 2 уровня и т.д.
    """
    frontier: set[int] = set(int(x) for x in root_user_ids if int(x) != 0)
    if not frontier:
        return set()

    visited: set[int] = set(frontier)
    descendants: set[int] = set()

    depth = 0
    while frontier and (max_depth is None or depth < max_depth):
        children = await Users.filter(referred_by__in=list(frontier)).values_list("id", flat=True)
        children_set = set(int(x) for x in children)
        new_nodes = children_set - visited
        if not new_nodes:
            break

        descendants.update(new_nodes)
        visited.update(new_nodes)
        frontier = new_nodes
        depth += 1

    return descendants


async def get_utm_stats(
    utm: str,
    *,
    moscow_today: date,
    indirect_max_depth: Optional[int] = None,
) -> UTMStats:
    """
    UTM-статистика с косвенным вкладом.

    - direct: пользователи с `Users.utm == utm`
    - indirect: все потомки direct по дереву `referred_by` (настраиваемая глубина)
    - total: direct ∪ indirect (без дублей)
    """
    direct_ids = await Users.filter(utm=utm).values_list("id", flat=True)
    direct_set = set(int(x) for x in direct_ids)

    indirect_set = await _descendants_user_ids(direct_set, max_depth=indirect_max_depth)
    # На всякий случай исключаем direct из indirect (если есть циклы/ручные правки)
    indirect_set -= direct_set

    direct_block = await _stats_for_user_ids(direct_set, moscow_today)
    indirect_block = await _stats_for_user_ids(indirect_set, moscow_today)
    total_block = await _stats_for_user_ids(direct_set | indirect_set, moscow_today)

    return UTMStats(direct=direct_block, indirect=indirect_block, total=total_block)



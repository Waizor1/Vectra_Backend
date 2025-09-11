from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bloobcat.funcs.validate import validate
from bloobcat.db.discounts import PersonalDiscount


router = APIRouter(prefix="/discounts", tags=["discounts"])


class DiscountItem(BaseModel):
    id: int
    percent: int
    is_permanent: bool
    remaining_uses: int
    expires_at: str | None
    source: str | None


@router.get("/my", response_model=List[DiscountItem])
async def my_discounts(user=Depends(validate)):
    rows = await PersonalDiscount.filter(user_id=user.id).order_by("-id")
    result: list[DiscountItem] = []
    for r in rows:
        result.append(
            DiscountItem(
                id=r.id,
                percent=r.percent,
                is_permanent=bool(r.is_permanent),
                remaining_uses=int(r.remaining_uses or 0),
                expires_at=(r.expires_at.isoformat() if r.expires_at else None),
                source=r.source,
            )
        )
    return result



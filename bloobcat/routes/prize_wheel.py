from typing import List

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from bloobcat.services.prize_wheel import PrizeWheelService
from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.bot.bot import bot
from bloobcat.settings import app_settings


router = APIRouter(prefix="/prize-wheel", tags=["prize-wheel"])


class PrizeWheelAttemptsResponse(BaseModel):
    attempts_available: int
    bonus_price: int
    bonus_attempts_available: int


class PrizeWheelSpinResponse(BaseModel):
    prize_type: str
    prize_name: str
    prize_value: str
    requires_admin: bool
    history_id: int


class PrizeWheelHistoryItem(BaseModel):
    prize_name: str
    prize_value: str
    is_claimed: bool
    is_rejected: bool
    created_at: str


class PrizeWheelConfigItem(BaseModel):
    type: str
    name: str
    value: str
    probability: float
    requires_admin: bool


async def get_current_user(user: Users = Depends(validate)) -> Users:
    return user


@router.get("/attempts", response_model=PrizeWheelAttemptsResponse)
async def get_attempts(user: Users = Depends(get_current_user)):
    attempts = await PrizeWheelService.get_user_attempts(user.id)
    price = int(app_settings.prize_wheel_spin_bonus_price or 0)
    bonus_attempts = (int(user.balance or 0) // price) if price > 0 else 0
    return PrizeWheelAttemptsResponse(
        attempts_available=attempts,
        bonus_price=price,
        bonus_attempts_available=bonus_attempts,
    )


class PrizeWheelSpinRequest(BaseModel):
    mode: str = "attempt"  # attempt | bonus


@router.post("/spin", response_model=PrizeWheelSpinResponse)
async def spin_wheel(payload: PrizeWheelSpinRequest | None = None, user: Users = Depends(get_current_user)):
    # 1) Явно проверяем наличие попыток, чтобы возвращать точную ошибку
    mode = (payload.mode if payload else "attempt").strip()
    if mode == "attempt":
        attempts = await PrizeWheelService.get_user_attempts(user.id)
        if attempts <= 0:
            raise HTTPException(status_code=400, detail="Нет доступных попыток")
    elif mode == "bonus":
        price = int(app_settings.prize_wheel_spin_bonus_price or 0)
        if price <= 0:
            raise HTTPException(status_code=400, detail="Колесо временно недоступно")
        if int(user.balance or 0) < price:
            raise HTTPException(status_code=400, detail=f"Недостаточно бонусного баланса (нужно {price}₽)")

    # 2) Проверяем, что есть активные призы
    config = await PrizeWheelService.get_prizes_config()
    if not config:
        raise HTTPException(status_code=400, detail="Колесо временно недоступно: нет активных призов")

    # 3) Запускаем спин (сервис сам атомарно спишет попытку и создаст запись истории)
    result = await PrizeWheelService.spin_wheel(user.id, bot=bot, mode=mode)
    if not result:
        # Если сюда попали, значит внутри сервиса произошла другая ошибка
        # (например, гонка сняла попытку раньше). Сообщим аккуратнее.
        raise HTTPException(status_code=400, detail="Не удалось выполнить вращение. Повторите попытку")
    return PrizeWheelSpinResponse(**result)


@router.get("/history", response_model=List[PrizeWheelHistoryItem])
async def get_history(limit: int = 10, user: Users = Depends(get_current_user)):
    history = await PrizeWheelService.get_user_history(user.id, limit)
    return [PrizeWheelHistoryItem(**item) for item in history]


@router.get("/config", response_model=List[PrizeWheelConfigItem])
async def get_prizes_config():
    config = await PrizeWheelService.get_prizes_config()
    return [PrizeWheelConfigItem(**item) for item in config]


@router.post("/initialize")
async def initialize_prizes():
    # TODO: ограничить админам
    await PrizeWheelService.initialize_default_prizes()
    return {"message": "Призы инициализированы"}



from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from cyberdog.db.users import User_Pydantic, Users
from cyberdog.funcs.validate import validate

router = APIRouter(
    prefix="/user",
    tags=["user"],
)


class UserUpdate(BaseModel):
    email: EmailStr


@router.get("")
async def check(user: Users = Depends(validate)):
    return await User_Pydantic.from_tortoise_orm(user)


@router.patch("")
async def update_user_profile(
    update_data: UserUpdate,
    user: Users = Depends(validate)
):
    """
    Обновляет email пользователя.
    """
    user.email = update_data.email
    await user.save()
    return await User_Pydantic.from_tortoise_orm(user)


@router.post("/unsubscribe")
async def unsubscribe(user: Users = Depends(validate)):
    user.is_subscribed = False
    await user.save()
    return {"status": "ok"}

"""Mock /v1/users/me —— 调试用,返回当前 token 关联用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import require_user

router = APIRouter(prefix="/v1/users", tags=["users"])


class UserInfo(BaseModel):
    id: str
    email: str
    plan: str = "personal"


@router.get("/me", response_model=UserInfo)
def me(email: str = Depends(require_user)) -> UserInfo:
    return UserInfo(id=f"u_{email.replace('@', '_').replace('.', '_')}", email=email)

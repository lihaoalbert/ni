"""Mock 鉴权:硬编码用户表 + 假 JWT。

生产环境的真实 ibi.ren 平台用真实 JWT 签名(Mock 阶段不引入
cryptographic 复杂度),只需要保证:
- access_token 形如 `<user_id>.<random>` 让测试可以解析
- refresh_token 同 access_token 一致(简单 Mock)
"""
from __future__ import annotations

import secrets
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

router = APIRouter(prefix="/v1/auth", tags=["auth"])


# ===== 用户表(Mock 内存) =====

_USERS: dict[str, str] = {
    "test@ni.app": "test1234",
}
_TOKENS: dict[str, str] = {}  # token -> email


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600


class RefreshRequest(BaseModel):
    refresh_token: str


def _mint_token(email: str) -> str:
    """签发假 token: `<email>.<random>` —— 测试易解析。"""
    token = f"{email}.{secrets.token_urlsafe(16)}"
    _TOKENS[token] = email
    return token


def _email_from_token(token: str) -> Optional[str]:
    """从 token 取 email。"""
    # 先查表(支持 refresh token)
    if token in _TOKENS:
        return _TOKENS[token]
    # 退化:解析 `<email>.<random>` 格式
    if "." in token:
        maybe_email = token.rsplit(".", 1)[0]
        if maybe_email in _USERS:
            return maybe_email
    return None


def require_user(authorization: Optional[str] = Header(default=None)) -> str:
    """FastAPI dependency:校验 Authorization header,返回 email。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "missing bearer token"}},
        )
    token = authorization.removeprefix("Bearer ").strip()
    email = _email_from_token(token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "invalid token"}},
        )
    return email


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest) -> TokenResponse:
    pw = _USERS.get(req.email)
    if pw is None or pw != req.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "wrong email or password"}},
        )
    return TokenResponse(
        access_token=_mint_token(req.email),
        refresh_token=_mint_token(req.email),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest) -> TokenResponse:
    if req.email in _USERS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "email_taken", "message": "email already registered"}},
        )
    _USERS[req.email] = req.password
    return TokenResponse(
        access_token=_mint_token(req.email),
        refresh_token=_mint_token(req.email),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest) -> TokenResponse:
    email = _email_from_token(req.refresh_token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "unauthorized", "message": "invalid refresh token"}},
        )
    return TokenResponse(
        access_token=_mint_token(email),
        refresh_token=_mint_token(email),
    )

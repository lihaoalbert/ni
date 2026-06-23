"""ibi.ren Mock — 鉴权 API 测试

覆盖:
1. POST /v1/auth/login 成功 → 返回 fake JWT
2. POST /v1/auth/login 错误密码 → 401
3. POST /v1/auth/register 创建用户
4. POST /v1/auth/refresh 续 token
5. GET /v1/users/me 需要 Bearer token
6. GET /v1/users/me 错误 token → 401
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ===== 登录 =====


def test_login_success(client: TestClient) -> None:
    """正确邮箱密码 → 200 + access_token"""
    response = client.post(
        "/v1/auth/login",
        json={"email": "test@ni.app", "password": "test1234"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] > 0


def test_login_wrong_password(client: TestClient) -> None:
    """错密码 → 401"""
    response = client.post(
        "/v1/auth/login",
        json={"email": "test@ni.app", "password": "wrong"},
    )
    assert response.status_code == 401
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "unauthorized"


def test_login_missing_fields(client: TestClient) -> None:
    """缺字段 → 422"""
    response = client.post("/v1/auth/login", json={"email": "x@x.com"})
    assert response.status_code == 422


# ===== Token 使用 =====


def _get_token(client: TestClient) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"email": "test@ni.app", "password": "test1234"},
    )
    return response.json()["access_token"]


def test_get_me_with_token(client: TestClient) -> None:
    """带 token → 200 + 用户信息"""
    token = _get_token(client)
    response = client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "email" in data


def test_get_me_without_token(client: TestClient) -> None:
    """不带 token → 401"""
    response = client.get("/v1/users/me")
    assert response.status_code == 401


def test_get_me_with_invalid_token(client: TestClient) -> None:
    """错 token → 401"""
    response = client.get(
        "/v1/users/me",
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert response.status_code == 401


# ===== Token 刷新 =====


def test_refresh_token(client: TestClient) -> None:
    """refresh_token 续 access_token"""
    login = client.post(
        "/v1/auth/login",
        json={"email": "test@ni.app", "password": "test1234"},
    )
    refresh = login.json()["refresh_token"]

    response = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


# ===== 注册 =====


def test_register_creates_user(client: TestClient) -> None:
    """新邮箱 → 注册成功"""
    import uuid
    new_email = f"new-{uuid.uuid4().hex[:8]}@ni.app"
    response = client.post(
        "/v1/auth/register",
        json={"email": new_email, "password": "newpass123"},
    )
    assert response.status_code == 201
    assert "access_token" in response.json()


def test_register_existing_email_fails(client: TestClient) -> None:
    """重复邮箱 → 409"""
    response = client.post(
        "/v1/auth/register",
        json={"email": "test@ni.app", "password": "whatever"},
    )
    assert response.status_code == 409

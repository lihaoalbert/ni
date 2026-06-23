"""ibi.ren Mock — IP 资源 API 测试

覆盖:
1. GET /v1/ips 列表 + 分页
2. GET /v1/ips/{id} 详情 + character
3. GET /v1/ips/{id} 不存在 → 404
4. GET /v1/cdn/{ip_id}/{size}.png 占位图
5. 字段约束(avatar_url / preview_url / character)
6. License 校验
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_token(client: TestClient) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"email": "test@ni.app", "password": "test1234"},
    )
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(auth_token: str) -> dict:
    return {"Authorization": f"Bearer {auth_token}"}


# ===== 列表 =====


def test_list_ips_returns_paginated(client: TestClient, auth_headers: dict) -> None:
    """列表 API 返回分页 items + total + has_more"""
    response = client.get("/v1/ips", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "has_more" in data
    assert isinstance(data["items"], list)
    assert len(data["items"]) > 0


def test_list_ips_requires_auth(client: TestClient) -> None:
    """列表 API 需要 token"""
    response = client.get("/v1/ips")
    assert response.status_code == 401


def test_list_ips_pagination(client: TestClient, auth_headers: dict) -> None:
    """分页参数工作"""
    response = client.get(
        "/v1/ips?page=1&page_size=2", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert len(data["items"]) <= 2


def test_list_ips_filter_by_status(client: TestClient, auth_headers: dict) -> None:
    """status 过滤参数"""
    response = client.get(
        "/v1/ips?status=active", headers=auth_headers
    )
    assert response.status_code == 200


def test_list_ip_item_shape(client: TestClient, auth_headers: dict) -> None:
    """列表项包含所有必填字段"""
    response = client.get("/v1/ips", headers=auth_headers)
    item = response.json()["items"][0]
    required_fields = [
        "id", "name", "avatar_url", "preview_url",
        "tags", "license_type", "downloaded_at",
    ]
    for field in required_fields:
        assert field in item, f"missing {field} in list item"


# ===== 详情 =====


def test_get_ip_detail(client: TestClient, auth_headers: dict) -> None:
    """详情 API 返回完整 character + assets"""
    # 先拿一个 ID
    listing = client.get("/v1/ips", headers=auth_headers).json()
    ip_id = listing["items"][0]["id"]

    response = client.get(f"/v1/ips/{ip_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    # character 子对象
    assert "character" in data
    char = data["character"]
    for field in [
        "id", "name", "personality_traits", "backstory",
        "speaking_style", "boundaries", "memory_seed",
    ]:
        assert field in char, f"missing character.{field}"

    # assets
    assert "assets" in data
    assets = data["assets"]
    assert "preview_2k_url" in assets

    # license
    assert "license" in data
    license_ = data["license"]
    assert "type" in license_
    assert "can_offline_use" in license_


def test_get_ip_not_found(client: TestClient, auth_headers: dict) -> None:
    """不存在的 IP → 404"""
    response = client.get(
        "/v1/ips/ip_does_not_exist", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ip_not_found"


def test_get_ip_requires_auth(client: TestClient) -> None:
    """详情 API 需要 token"""
    response = client.get("/v1/ips/ip_001")
    assert response.status_code == 401


# ===== License 校验 =====


def test_license_check(client: TestClient, auth_headers: dict) -> None:
    """License 校验返回 valid + can_offline_use"""
    listing = client.get("/v1/ips", headers=auth_headers).json()
    ip_id = listing["items"][0]["id"]

    response = client.get(
        f"/v1/ips/{ip_id}/license", headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert "valid" in data
    assert "type" in data
    assert "can_offline_use" in data
    assert data["valid"] is True


def test_license_for_unknown_ip(client: TestClient, auth_headers: dict) -> None:
    """不存在的 IP → 404"""
    response = client.get(
        "/v1/ips/unknown_xxx/license", headers=auth_headers
    )
    assert response.status_code == 404


# ===== CDN 占位图 =====


def test_cdn_image_serves_png(client: TestClient, auth_headers: dict) -> None:
    """CDN 端点返回 PNG 二进制"""
    listing = client.get("/v1/ips", headers=auth_headers).json()
    ip_id = listing["items"][0]["id"]

    # 公开端点,不需要 token(模拟公开 CDN)
    response = client.get(f"/v1/cdn/{ip_id}/2k.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:4] == b"\x89PNG"  # PNG magic bytes


def test_cdn_image_for_unknown_ip(client: TestClient) -> None:
    """未知 IP → 404"""
    response = client.get("/v1/cdn/unknown_xxx/2k.png")
    assert response.status_code == 404


def test_cdn_image_supports_sizes(client: TestClient, auth_headers: dict) -> None:
    """支持多种尺寸(256, 1k, 2k, 4k)"""
    listing = client.get("/v1/ips", headers=auth_headers).json()
    ip_id = listing["items"][0]["id"]

    for size in ["256", "1k", "2k", "4k"]:
        response = client.get(f"/v1/cdn/{ip_id}/{size}.png")
        assert response.status_code == 200, f"size {size} failed"
        assert response.content[:4] == b"\x89PNG"

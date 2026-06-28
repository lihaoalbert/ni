"""IP 资源列表 + 详情 + License 校验。

返回结构按 /Users/app/ni/docs/ips-api-requirements.md:
- GET /v1/ips                  分页列表
- GET /v1/ips/{ip_id}          完整 character + assets
- GET /v1/ips/{ip_id}/license  License 校验结果
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import require_user
from app.data import get_ip, list_ips

router = APIRouter(prefix="/v1/ips", tags=["ips"])


def _resolve_base_url(request: Request) -> str:
    """从请求头推断对外可访问的 base URL — iOS 端要拿这个去拉头像 / 资源。
    nginx 反代会带 X-Forwarded-Proto + Host;本地测试则回退 localhost。
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8001"
    return f"{proto}://{host}"


def _to_list_item(ip: dict, base_url: str) -> dict:
    """列表项字段(扁平,不含 character/assets/license 完整对象)。"""
    return {
        "id": ip["id"],
        "name": ip["name"],
        "avatar_url": f"{base_url}/v1/cdn/{ip['id']}/256.png",
        "preview_url": f"{base_url}/v1/cdn/{ip['id']}/1k.png",
        "tags": ip["tags"],
        "voice_id": ip["character"]["voice_id"],
        "personality_summary": ip["personality_summary"],
        "license_type": ip["license"]["type"],
        "license_expires_at": ip["license"].get("expires_at"),
        "downloaded_at": ip.get("downloaded_at"),
    }


def _to_detail(ip: dict, base_url: str) -> dict:
    lic = ip["license"]
    return {
        "id": ip["id"],
        "name": ip["name"],
        "avatar_url": f"{base_url}/v1/cdn/{ip['id']}/256.png",
        "preview_url": f"{base_url}/v1/cdn/{ip['id']}/2k.png",
        "tags": ip["tags"],
        "voice_id": ip["character"]["voice_id"],
        "personality_summary": ip["personality_summary"],
        "license_type": lic["type"],
        "license_expires_at": lic.get("expires_at"),
        "downloaded_at": ip.get("downloaded_at"),
        "character": ip["character"],
        "license": {**lic, "can_offline_use": lic["type"] == "personal_perpetual"},
        "assets": {
            "preview_2k_url": f"{base_url}/v1/cdn/{ip['id']}/2k.png",
            "preview_4k_url": f"{base_url}/v1/cdn/{ip['id']}/4k.png",
            "voice_sample_url": ip["assets"]["voice_sample_url"],
            "expression_set_url": ip["assets"].get("expression_set_url"),
        },
    }


@router.get("")
def list_endpoint(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    _email: str = Depends(require_user),
) -> dict:
    base_url = _resolve_base_url(request)
    items = list_ips(status=status)
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]
    return {
        "items": [_to_list_item(it, base_url) for it in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": end < total,
    }


@router.get("/{ip_id}")
def detail(
    request: Request,
    ip_id: str,
    _email: str = Depends(require_user),
) -> dict:
    ip = get_ip(ip_id)
    if not ip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "ip_not_found", "message": f"IP {ip_id} not found"}},
        )
    return _to_detail(ip, _resolve_base_url(request))


@router.get("/{ip_id}/license")
def license_check(
    ip_id: str,
    _email: str = Depends(require_user),
) -> dict:
    ip = get_ip(ip_id)
    if not ip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "ip_not_found", "message": f"IP {ip_id} not found"}},
        )
    lic = ip["license"]
    return {
        "valid": True,
        "type": lic["type"],
        "scope": lic["scope"],
        "download_quota_remaining": lic["download_quota"] - lic.get("download_used", 0),
        "expires_at": lic.get("expires_at"),
        "can_offline_use": lic["type"] == "personal_perpetual",
    }

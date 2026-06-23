"""占位 PNG 端点:`/v1/cdn/{ip_id}/{size}.png` —— 公开访问,无需 token。

真实平台这一路是 CDN(返回签名 URL 链到 S3/OSS 等)。Mock 阶段用
ffmpeg 生成的占位图(测试验证 PNG magic bytes 即可)。

支持的 size: 256 / 1k / 2k / 4k。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

from app.data import get_ip

router = APIRouter(prefix="/v1/cdn", tags=["cdn"])

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_SIZE_TO_RES = {"256": 256, "1k": 1024, "2k": 2048, "4k": 4096}


def _color_for(ip_id: str) -> tuple[int, int, int]:
    """基于 ip_id 哈希生成稳定 RGB,让每个 IP 占位图颜色不一样。"""
    h = hashlib.md5(ip_id.encode()).hexdigest()
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def generate_placeholder(ip_id: str, size_label: str) -> Path:
    """生成占位 PNG(ffmpeg lavfi),写到 assets/。同 IP+size 缓存。"""
    if size_label not in _SIZE_TO_RES:
        raise ValueError(f"unsupported size: {size_label}")
    px = _SIZE_TO_RES[size_label]
    r, g, b = _color_for(ip_id)
    out = _ASSETS_DIR / f"{ip_id}_{size_label}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0:
        return out
    # ffmpeg -f lavfi color 不可写 PNG,改用 -y 单帧截图
    import subprocess

    color_str = f"0x{r:02x}{g:02x}{b:02x}"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c={color_str}:s={px}x{px}:d=1",
        "-frames:v", "1",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


@router.get("/{ip_id}/{size}.png")
def serve_image(ip_id: str, size: str) -> Response:
    if size not in _SIZE_TO_RES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "invalid_size", "message": f"size {size} not supported"}},
        )
    if not get_ip(ip_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "ip_not_found", "message": f"IP {ip_id} not found"}},
        )
    path = generate_placeholder(ip_id, size)
    return Response(content=path.read_bytes(), media_type="image/png")

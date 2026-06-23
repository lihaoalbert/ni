"""FastAPI 入口 — 聚合 auth / users / ips / cdn 路由。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app import auth, cdn, ips, users

app = FastAPI(
    title="ibi.ren Mock",
    description="ibi.ren 平台 API Mock —— 并行开发期使用",
    version="0.1.0",
)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(ips.router)
app.include_router(cdn.router)


@app.exception_handler(HTTPException)
async def unwrap_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """FastAPI 默认把 HTTPException 包装成 `{"detail": ...}`,展开成 `{"error": ...}`
    以匹配 ibi.ren 真实平台的错误响应格式(参见 docs/ips-api-requirements.md §7)。"""
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    elif isinstance(detail, dict):
        body = {"error": {"code": "http_error", "message": str(detail)}}
    else:
        body = {"error": {"code": "http_error", "message": str(detail)}}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}

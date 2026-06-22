"""FastAPI 应用入口"""
from __future__ import annotations

from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.config import get_settings
from app.logging_setup import setup_logging

settings = get_settings()

# Day 6 — 初始化结构化日志（生产可切 JSON）
# 开关：环境变量 LOG_JSON=1
import os
setup_logging(
    level=settings.log_level,
    json_format=os.environ.get("LOG_JSON", "0") == "1",
)

app = FastAPI(
    title="Companion AI Backend",
    description="AI 数字人陪伴 App 后端 — Claude 学习项目",
    version="0.1.0",
)

app.include_router(chat_router, tags=["chat"])


@app.get("/")
async def root() -> dict:
    return {
        "name": "Companion AI Backend",
        "version": "0.1.0",
        "docs": "/docs",
        "env": settings.app_env,
    }

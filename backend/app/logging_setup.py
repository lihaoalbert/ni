"""结构化日志配置

设计目标：
1. 默认 human-readable（开发友好）
2. 可切 JSON 格式（生产接入 ELK / Loki）
3. access logger 记录每次 chat 调用
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


def setup_logging(level: str = "INFO", json_format: bool = False) -> None:
    """初始化根 logger

    Args:
        level: 日志级别
        json_format: True 输出 JSON，False 输出 human-readable
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    # 移除已有 handler（uvicorn 装过的）
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(HumanFormatter())
    root.addHandler(handler)


class HumanFormatter(logging.Formatter):
    """开发用：易读的彩色日志"""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        # extra 字段以 key=value 形式输出
        extras = []
        for k, v in record.__dict__.items():
            if k not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                extras.append(f"{k}={v}")
        extra_str = " " + " ".join(extras) if extras else ""
        return f"{ts} {record.levelname:<5} {record.name}: {msg}{extra_str}"


class JsonFormatter(logging.Formatter):
    """生产用：JSON Lines 格式（每行一个 JSON 对象）"""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exception"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                obj[k] = v
        return json.dumps(obj, ensure_ascii=False)


# ===== Access Logger =====


@dataclass
class CallMetrics:
    """单次 chat 调用的指标"""

    request_id: str = ""
    user_id: str = ""
    character_id: str = ""
    status: str = "ok"  # ok / error
    error: str = ""
    iterations: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: float = 0.0
    extra: dict = field(default_factory=dict)


access_logger = logging.getLogger("access")


@contextmanager
def log_chat_call(
    request_id: str,
    user_id: str,
    character_id: str,
) -> Iterator[CallMetrics]:
    """上下文管理器 — 自动记录每次 chat 调用

    用法：
        with log_chat_call(req_id, user, char) as m:
            result = await agent.run(...)
            m.iterations = result.iterations
            m.tool_calls = len(result.tool_calls)
            ...
    """
    metrics = CallMetrics(
        request_id=request_id,
        user_id=user_id,
        character_id=character_id,
    )
    start = time.monotonic()
    try:
        yield metrics
    except Exception as e:
        metrics.status = "error"
        metrics.error = f"{type(e).__name__}: {e!s}"
        raise
    finally:
        metrics.latency_ms = round((time.monotonic() - start) * 1000, 1)
        access_logger.info(
            "chat_call",
            extra={
                "request_id": metrics.request_id,
                "user_id": metrics.user_id,
                "character_id": metrics.character_id,
                "status": metrics.status,
                "error": metrics.error,
                "iterations": metrics.iterations,
                "tool_calls": metrics.tool_calls,
                "input_tokens": metrics.input_tokens,
                "output_tokens": metrics.output_tokens,
                "cache_creation_tokens": metrics.cache_creation_tokens,
                "cache_read_tokens": metrics.cache_read_tokens,
                "latency_ms": metrics.latency_ms,
                **metrics.extra,
            },
        )

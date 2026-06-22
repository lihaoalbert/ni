"""Eval runner — Day 7

执行一个 EvalCase：按 turns 顺序调 /chat，对每轮跑断言。

设计：
- 用 FastAPI TestClient：测试经过完整 endpoint stack（验证 + 鉴权 + 错误映射）
- 状态隔离：每个 case 用独立 user_id，所以即使共用 stores 也不会污染
- 延迟测量：每轮发请求前后取 time.monotonic()
- 不依赖任何真实 LLM：默认 mock 掉 LLM provider；EVAL_LIVE=1 时用真模型
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi.testclient import TestClient

from app.evals.schemas import EvalCase, EvalResult, TurnResult

logger = logging.getLogger(__name__)


def run_case(case: EvalCase, client: TestClient) -> EvalResult:
    """同步执行一个 case — 在每个 turn 上跑断言

    Args:
        case: EvalCase 模型
        client: 已配置好的 FastAPI TestClient

    Returns:
        EvalResult 含每轮实际结果 + 失败原因
    """
    turn_results: list[TurnResult] = []
    total_start = time.monotonic()

    for turn in case.turns:
        start = time.monotonic()
        resp = client.post(
            "/chat",
            json={
                "user_id": case.user_id,
                "character_id": case.character_id,
                "message": turn.user,
            },
        )
        latency_ms = (time.monotonic() - start) * 1000

        if resp.status_code != 200:
            turn_results.append(
                TurnResult(
                    user=turn.user,
                    reply=f"<http {resp.status_code}: {resp.text[:200]}>",
                    tools_called=[],
                    iterations=0,
                    latency_ms=latency_ms,
                    failures=[f"http_{resp.status_code}"],
                )
            )
            continue

        body: dict[str, Any] = resp.json()
        reply = body.get("reply", "")
        tools_called = [op.get("name", "?") for op in body.get("memory_ops", [])]
        iterations = body.get("iterations", 1)

        result = TurnResult(
            user=turn.user,
            reply=reply,
            tools_called=tools_called,
            iterations=iterations,
            latency_ms=latency_ms,
            input_tokens=body.get("input_tokens", 0),
            output_tokens=body.get("output_tokens", 0),
        )

        # === 跑断言 ===
        failures: list[str] = []

        for needle in turn.expect.must_contain:
            if needle not in reply:
                failures.append(f"must_contain missing: {needle!r}")

        for needle in turn.expect.must_not_contain:
            if needle in reply:
                failures.append(f"must_not_contain present: {needle!r}")

        for tool in turn.expect.tools_called:
            if tool not in tools_called:
                failures.append(f"tools_called missing: {tool!r}")

        if turn.expect.min_iterations and iterations < turn.expect.min_iterations:
            failures.append(
                f"iterations too low: got {iterations}, expected >= {turn.expect.min_iterations}"
            )

        if turn.expect.max_iterations and iterations > turn.expect.max_iterations:
            failures.append(
                f"iterations too high: got {iterations}, expected <= {turn.expect.max_iterations}"
            )

        if turn.expect.max_latency_ms and latency_ms > turn.expect.max_latency_ms:
            failures.append(
                f"latency too high: got {latency_ms:.0f}ms, expected <= {turn.expect.max_latency_ms:.0f}ms"
            )

        result.failures = failures
        turn_results.append(result)

    return EvalResult(
        case_name=case.name,
        character_id=case.character_id,
        user_id=case.user_id,
        turns=turn_results,
        total_latency_ms=(time.monotonic() - total_start) * 1000,
    )


def is_live_mode() -> bool:
    """是否启用真实 LLM 跑 eval — 默认 false（mock 模式）"""
    return os.environ.get("EVAL_LIVE", "0") == "1"


def live_mode_warning() -> str:
    if is_live_mode():
        return "EVAL_LIVE=1 — 调用真实 Claude API（会消耗 token）"
    return "EVAL_LIVE=0 — 使用 mock LLM provider（CI 友好）"

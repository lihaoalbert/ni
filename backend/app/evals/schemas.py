"""Evals 数据模型 — Day 7

设计原则：YAML 写得简单，断言类型明确。

支持的断言：
- must_contain       : 回复必须包含这些字符串（大小写敏感）
- must_not_contain   : 回复不能包含这些字符串
- tools_called       : 必须调过这些工具（按名字）
- min_iterations     : Agent 至少循环了 N 轮（用来强制必须调工具）
- max_iterations     : Agent 最多循环 N 轮
- max_latency_ms     : 单轮最大延迟（性能门禁）

不在 Day 7 范围（先简化）：
- LLM-as-judge（用另一个 LLM 给回复打分）— 留到第二阶段
- fuzzy match（编辑距离相似度）— 当前必须 contain / 不 contain
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TurnExpect(BaseModel):
    """一轮对话的期望结果"""

    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    min_iterations: int = 0
    max_iterations: int = 0  # 0 = 不限制
    max_latency_ms: float = 0  # 0 = 不限制


class Turn(BaseModel):
    """一轮对话：用户说一句 + 期望什么结果"""

    user: str
    expect: TurnExpect = Field(default_factory=TurnExpect)


class EvalCase(BaseModel):
    """一个完整评测用例 — 通常包含 1~5 轮多轮对话"""

    name: str
    character_id: str
    user_id: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)  # 便于按类型筛选（persona/memory/boundary/...）
    turns: list[Turn] = Field(min_length=1)


class TurnResult(BaseModel):
    """一轮对话的实际执行结果"""

    user: str
    reply: str
    tools_called: list[str]
    iterations: int
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    failures: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.failures) == 0


class EvalResult(BaseModel):
    """一个评测用例的完整结果"""

    case_name: str
    character_id: str
    user_id: str
    turns: list[TurnResult]
    total_latency_ms: float = 0

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.turns)

    @property
    def failure_count(self) -> int:
        return sum(1 for t in self.turns if not t.passed)

    def summary_lines(self) -> list[str]:
        """人类可读的执行结果 — 用于 pytest 失败信息"""
        lines = [f"  case: {self.case_name} ({'PASS' if self.passed else 'FAIL'})"]
        for i, t in enumerate(self.turns, 1):
            status = "PASS" if t.passed else "FAIL"
            lines.append(
                f"    turn {i}: {status} | iter={t.iterations} tools={t.tools_called} "
                f"latency={t.latency_ms:.0f}ms"
            )
            if t.failures:
                for f in t.failures:
                    lines.append(f"      - {f}")
            # 截短显示回复
            snippet = t.reply[:80] + "..." if len(t.reply) > 80 else t.reply
            lines.append(f"      reply: {snippet!r}")
        return lines


# ===== 工具函数 =====


def case_to_yaml_dict(case: EvalCase) -> dict[str, Any]:
    """EvalCase → 适合 dump YAML 的 dict（去掉空字段）"""
    out: dict[str, Any] = {
        "name": case.name,
        "character_id": case.character_id,
        "user_id": case.user_id,
    }
    if case.description:
        out["description"] = case.description
    if case.tags:
        out["tags"] = case.tags
    turns_out = []
    for t in case.turns:
        expect: dict[str, Any] = {}
        if t.expect.must_contain:
            expect["must_contain"] = t.expect.must_contain
        if t.expect.must_not_contain:
            expect["must_not_contain"] = t.expect.must_not_contain
        if t.expect.tools_called:
            expect["tools_called"] = t.expect.tools_called
        if t.expect.min_iterations:
            expect["min_iterations"] = t.expect.min_iterations
        if t.expect.max_iterations:
            expect["max_iterations"] = t.expect.max_iterations
        if t.expect.max_latency_ms:
            expect["max_latency_ms"] = t.expect.max_latency_ms
        turns_out.append({"user": t.user, "expect": expect})
    out["turns"] = turns_out
    return out

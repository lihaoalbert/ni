"""Smart mock LLM provider for evals — Day 7

设计目标：
- 让 eval 在 CI 里跑得动（不用 API key、不消耗 token）
- 模拟"好"的角色行为:让 persona / memory / boundary 用例通过
- 不是要取代真模型 —— 是框架的 demo 道具

EVAL_LIVE=1 时不装载这个 mock,改用真 LLM。
"""
from __future__ import annotations

import re
from typing import AsyncIterator

from app.llm.base import ChatResponse, LLMProvider, StreamEvent, ToolCall


# ===== 模式识别 =====

# 用户自我介绍 — 触发 save_fact
NAME_PATTERNS = [
    re.compile(r"我叫(\w+)"),
]
WORK_PATTERNS = [
    re.compile(r"我是(一名|一个)?(\w+(?:工程师|设计师|经理|医生|教师|学生|律师|会计))"),
    re.compile(r"(?:我)?是(一名|一个)?(\w+(?:工程师|设计师|经理|医生|教师|学生|律师|会计))"),
]
PREF_PATTERNS = [
    re.compile(r"我喜欢(\w+)"),
    re.compile(r"我(?:最近)?在听(\w+)"),
    re.compile(r"我最近在(\w+)(?:音乐|书|电影)?"),
]

# 用户提问 — 用于 recall
ASK_NAME = re.compile(r"叫什么|叫什么名字")
ASK_WORK = re.compile(r"做什么工作|什么职业|职业是")
ASK_PREF = re.compile(r"喜欢什么|听什么|在听什么|什么音乐")

# 人设问题的固定回复（基于苏晚的角色）
PERSONA_ANSWERS = {
    "name": "我叫苏晚。",
    "work": "我做建筑设计师，最近在做城市更新方向。",
    "pet": "我养了一只橘猫，叫小满。",
    "location": "我住上海徐汇，独居。",
}

# 边界话题关键词 → 拒绝模板
MEDICAL_KEYWORDS = ["头疼", "发烧", "感冒", "咳嗽", "血压", "血糖", "胸痛", "胃痛", "药"]
POLITICAL_KEYWORDS = ["政治", "政府", "总统", "大选", "敏感", "主义", "政党"]


def _extract_text(msg: dict) -> str:
    """Extract plain text from a message (handle string / text blocks / tool_result)"""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    # 把 tool_use 块里的 input 也算作文本
                    inp = block.get("input", {})
                    if isinstance(inp, dict):
                        for v in inp.values():
                            if isinstance(v, str):
                                parts.append(v)
                # tool_result 块:跳过
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


def _all_user_text(messages: list[dict]) -> str:
    """Get all user-role text content from the message history"""
    parts: list[str] = []
    for m in messages:
        if m.get("role") == "user":
            parts.append(_extract_text(m))
    return " ".join(parts)


class MockEvalProvider(LLMProvider):
    """Mock LLM — 通过 inspect 消息历史决定回复

    适用范围:测试苏晚的 persona / memory / boundary
    加新角色时,需要扩展 PERSONA_ANSWERS

    设计细节:跨调用记住刚 save_fact 的内容,
    这样 tool_use → tool_result 后的下一轮能给出正确 follow-up。
    """

    model = "mock-eval"

    def __init__(self):
        self._pending_text_after_tool: str | None = None

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        return self._decide(messages, tools)

    def _decide(self, messages: list[dict], tools: list[dict] | None) -> ChatResponse:
        """核心决策 — 检查当前消息,决定 save_fact 还是回答"""
        # === 0) 上一轮是 tool_use? 给一个 follow-up 文本 ===
        if self._pending_text_after_tool is not None:
            text = self._pending_text_after_tool
            self._pending_text_after_tool = None
            return ChatResponse(
                text=text,
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=10,
                model=self.model,
            )

        current = messages[-1] if messages else {"role": "user", "content": ""}
        current_text = _extract_text(current)

        # current 是纯 tool_result 块(没有 text / tool_use 字段) — 给个简短确认
        if not current_text.strip():
            return ChatResponse(
                text="好的。",
                stop_reason="end_turn",
                input_tokens=50, output_tokens=5, model=self.model,
            )

        history_text = _all_user_text(messages[:-1]) if len(messages) > 1 else ""

        # === 1) 提问 — 召回 (优先于 save,避免 "我最近在听什么" 触发 save) ===
        if ASK_NAME.search(current_text):
            for p in NAME_PATTERNS:
                m = p.search(history_text)
                if m:
                    return ChatResponse(
                        text=f"你叫{m.group(1)}呀。",
                        stop_reason="end_turn",
                        input_tokens=50, output_tokens=8, model=self.model,
                    )

        if ASK_WORK.search(current_text):
            for p in WORK_PATTERNS:
                m = p.search(history_text)
                if m:
                    return ChatResponse(
                        text=f"你是{m.group(2)}。",
                        stop_reason="end_turn",
                        input_tokens=50, output_tokens=8, model=self.model,
                    )

        if ASK_PREF.search(current_text):
            for p in PREF_PATTERNS:
                m = p.search(history_text)
                if m:
                    return ChatResponse(
                        text=f"你最近在{m.group(0)}。",
                        stop_reason="end_turn",
                        input_tokens=50, output_tokens=8, model=self.model,
                    )

        # === 2) 用户分享信息 → save_fact ===
        name_match = next(
            (p.search(current_text) for p in NAME_PATTERNS if p.search(current_text)),
            None,
        )
        work_match = next(
            (p.search(current_text) for p in WORK_PATTERNS if p.search(current_text)),
            None,
        )
        pref_match = next(
            (p.search(current_text) for p in PREF_PATTERNS if p.search(current_text)),
            None,
        )

        if name_match or work_match or pref_match:
            tool_calls: list[ToolCall] = []
            saved_descs: list[str] = []
            idx = 0
            if name_match:
                idx += 1
                tool_calls.append(
                    ToolCall(
                        id=f"t{idx}",
                        name="save_fact",
                        input={"content": f"用户叫{name_match.group(1)}", "category": "basic"},
                    )
                )
                saved_descs.append(f"叫{name_match.group(1)}")
            if work_match:
                idx += 1
                tool_calls.append(
                    ToolCall(
                        id=f"t{idx}",
                        name="save_fact",
                        input={"content": f"用户是{work_match.group(2)}", "category": "work"},
                    )
                )
                saved_descs.append(f"是{work_match.group(2)}")
            if pref_match:
                idx += 1
                tool_calls.append(
                    ToolCall(
                        id=f"t{idx}",
                        name="save_fact",
                        input={"content": f"用户喜欢{pref_match.group(1)}", "category": "preference"},
                    )
                )
                saved_descs.append(f"喜欢{pref_match.group(1)}")

            self._pending_text_after_tool = f"嗯,我记住了,你{'、'.join(saved_descs)}。"

            return ChatResponse(
                text="好的,我记一下。",
                stop_reason="tool_use",
                input_tokens=50, output_tokens=15, model=self.model,
                tool_calls=tool_calls,
            )

        # === 3) 人设问题 ===
        if "叫什么名字" in current_text or "你是谁" in current_text:
            return ChatResponse(
                text=PERSONA_ANSWERS["name"],
                stop_reason="end_turn", input_tokens=50, output_tokens=10, model=self.model,
            )
        if "工作" in current_text or "职业" in current_text or "做什么" in current_text:
            return ChatResponse(
                text=PERSONA_ANSWERS["work"],
                stop_reason="end_turn", input_tokens=50, output_tokens=20, model=self.model,
            )
        if "宠物" in current_text or ("养" in current_text and "猫" not in current_text) or "小满" in current_text:
            return ChatResponse(
                text=PERSONA_ANSWERS["pet"],
                stop_reason="end_turn", input_tokens=50, output_tokens=15, model=self.model,
            )
        if "住" in current_text or "哪里" in current_text:
            return ChatResponse(
                text=PERSONA_ANSWERS["location"],
                stop_reason="end_turn", input_tokens=50, output_tokens=12, model=self.model,
            )

        # === 4) 边界话题 ===
        if any(k in current_text for k in MEDICAL_KEYWORDS):
            return ChatResponse(
                text="我不太懂医疗,这种情况最好去看医生,让专业的人来诊断。",
                stop_reason="end_turn",
                input_tokens=50, output_tokens=20, model=self.model,
            )
        if any(k in current_text for k in POLITICAL_KEYWORDS):
            return ChatResponse(
                text="政治话题我不太擅长,咱们换个方向聊聊吧?",
                stop_reason="end_turn",
                input_tokens=50, output_tokens=15, model=self.model,
            )

        # === 5) 默认 ===
        return ChatResponse(
            text="嗯,让我想想。",
            stop_reason="end_turn",
            input_tokens=50, output_tokens=8, model=self.model,
        )

    async def stream_chat(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式 mock — 把 _decide 翻译成 SSE 事件流"""
        response = self._decide(messages, tools)
        yield StreamEvent(
            type="message_start",
            input_tokens=response.input_tokens,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            model=response.model,
        )
        if response.text:
            for ch in response.text:
                yield StreamEvent(type="text", text=ch)
        for tc in response.tool_calls:
            yield StreamEvent(type="tool_use_start", tool_id=tc.id, tool_name=tc.name)
            yield StreamEvent(
                type="tool_use_input_delta",
                tool_id=tc.id,
                partial_json=str(tc.input).replace("'", '"'),
            )
        yield StreamEvent(
            type="message_stop",
            stop_reason=response.stop_reason,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            model=response.model,
        )

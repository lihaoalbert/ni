# Day 3 学习笔记：Tool Use + 永久记忆 + Agent 循环

## 今日产出
- LLM Provider 扩展支持 tools 参数
- Memory 模块（schemas + store + 去重 + 检索）
- 4 个 Agent 工具：`search_memory` / `save_fact` / `list_user_facts` / `forget_fact`
- AgentRuntime 简易循环（tool_use → 执行 → 续调）
- `/chat` 接入 Agent + 会话历史
- 调试端点：`/memories/{user_id}` 查记忆
- **49 个测试全过**

## 实测：苏晚真的"记住"了

| 轮次 | 用户 | 苏晚 | 工具调用 |
|---|---|---|---|
| T1 | "你好苏晚，我叫小明，是软件工程师，喜欢爵士乐和猫" | 自然聊天，没存 | 无 |
| T2 | "请帮我记住，我叫小明，29岁，是软件工程师，住在上海，喜欢爵士乐和猫" | "记住了，小明…" | save_fact ×2 (basic + preference) |
| T3 | "你还记得我叫什么？做什么工作？住哪吗？" | "当然记得啦，你叫小明，29岁，软件工程师，住在上海。喜欢爵士乐和猫。" | 无（直接用对话历史） |

记忆系统查得到：
```json
{
  "count": 2,
  "facts": [
    {"category": "preference", "content": "用户喜欢爵士乐和猫"},
    {"category": "basic", "content": "用户叫小明，29岁，是软件工程师，住在上海"}
  ]
}
```

## 学到的 Claude 核心能力

### 1. Tool Use 协议
**请求格式**：在 `client.messages.create()` 加 `tools` 参数：
```python
tools = [{
    "name": "search_memory",
    "description": "在用户的长期记忆里搜索相关事实...",
    "input_schema": {"type": "object", "properties": {...}, "required": [...]}
}]
```

**响应格式**：response.content 是块数组：
```python
response.content = [
    TextBlock(text="让我查一下..."),
    ToolUseBlock(id="toolu_xxx", name="search_memory", input={"query": "..."})
]
```

**续调格式**：把 assistant 的完整 content 追加 + tool_result 块追加为 user 消息：
```python
messages.append({
    "role": "assistant",
    "content": [
        {"type": "text", "text": "让我查一下"},
        {"type": "tool_use", "id": "toolu_xxx", "name": "...", "input": {...}}
    ]
})
messages.append({
    "role": "user",
    "content": [
        {"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "..."}
    ]
})
```

### 2. Tool description 是"教学"的关键
模型决定要不要调工具、何时调、传什么参数，**完全靠 description**。所以：
- **说什么时候用**（"当用户分享了值得记住的信息..."）
- **说什么时候不用**（"不要保存纯问候..."）
- **输入描述要清晰**（每个 property 都加 description）

description 写不好，模型就不调或乱调。

### 3. stop_reason 流转
| 状态 | 处理 |
|---|---|
| `end_turn` | 正常结束，返回 text |
| `tool_use` | 执行工具，循环续调 |
| `max_tokens` | 输出截断，可能丢失信息 |

简易 Agent 循环代码：
```python
while iterations < max:
    response = await llm.chat(messages, tools=...)
    if response.stop_reason == "end_turn":
        return response.text
    if response.stop_reason == "tool_use":
        messages.append(assistant_with_tool_use_blocks(response))
        messages.append(user_with_tool_results(...))
        continue
```

### 4. 系统提示的"必须"二字
第一次 prompt 我写的："主动调用 save_fact 保存" → 模型很少调
改后写的："**用户每次分享个人信息，你必须立刻调用 save_fact 保存**" → 模型还是会判断是否"值得存"

**学到**：Claude 对"必须"的理解是"我真的认为必要才做"，不是"无条件执行"。产品设计上：
- 想让模型无条件存 → 自动提取（Haiku 后台跑）
- 想让模型判断 → 提示要更具体（"29 岁软件工程师"明确值得存）

### 5. 会话历史 vs 长期记忆（关键区分）
**会话历史**（ConversationStore）：最近 20 轮的完整对话
- 输入大但实时
- 不跨会话

**长期记忆**（MemoryStore）：抽取出的事实
- 输入小但语义浓缩
- 跨会话

**正确的设计**：
- 当前会话用 history 注入
- 跨会话用 search_memory 检索
- 二者结合：messages = history + retrieved_memories + current_message

### 6. user_id 注入模式
工具定义 schema 里**不包含** user_id — 由 runtime 自动注入：
```python
async def execute(self, name, tool_input, user_id):  # user_id 由 runtime 传
    ...
```

避免：
- 模型编造 user_id
- 用户能查询别人记忆
- 工具 schema 污染

## 决策记录

**Decision 1**：进程内 InMemoryStore + ConversationStore
- Day 3 简化实现，重启即丢
- 后续接 PostgreSQL + Qdrant，接口不变

**Decision 2**：事实去重 = (user_id, category, content) 三元组
- 避免用户重复说"我是工程师"导致 100 条相同事实
- 不做语义去重（"软件工程师" vs "程序员" 视为不同），等向量层做

**Decision 3**：搜索算法 = Jaccard 相似度 × confidence
- 中文字符级匹配
- Day 3 够用；Day 5 接 embedding

**Decision 4**：max_iterations = 5
- 防止无限循环
- 实际多轮对话很少超过 3 轮

**Decision 5**：Agent prompt 在 character prompt 里，不在 agent module
- 角色知道自己有记忆工具（人设的一部分）
- 不同角色可以有不同的工具使用偏好

## 项目结构变化
```
backend/
├── app/
│   ├── memory/                  # 🆕
│   │   ├── schemas.py           # MemoryFact / FactCategory
│   │   └── store.py             # InMemoryStore + ConversationStore
│   ├── agent/                   # 🆕
│   │   ├── tools.py             # 4 个工具定义 + 执行器
│   │   └── runtime.py           # Agent 循环
│   ├── llm/
│   │   ├── base.py              # 🔧 增加 tool_calls 字段 + tools 参数
│   │   └── claude_provider.py   # 🔧 解析 tool_use 块
│   ├── api/chat.py              # 🔧 用 AgentRuntime，注入历史
│   └── characters/prompt.py     # 🔧 增加"长期记忆"段
└── tests/
    ├── test_memory.py           # 🆕 17 个测试
    └── test_agent.py            # 🆕 11 个测试
```

## 待办（Day 4+）
- [ ] Day 4：Prompt Caching — system prompt 已吃 600+ tokens，加 `cache_control` 应省一大笔
- [ ] Day 5：流式响应（SSE）
- [ ] Day 6：错误处理 + 重试 + 日志
- [ ] Day 7：evals
- [ ] 后接：PostgreSQL + Qdrant 替换 InMemoryStore

## ⚠️ 性能观察
T1: input_tokens=1252（无工具调用）
T2: input_tokens=???（3 轮迭代）

system prompt 单条 ~600 tokens。**Day 4 必须上 Prompt Caching**，否则每条消息都重付一遍。

## 启动命令
```bash
cd backend
uv run uvicorn app.main:app
# T1: 自我介绍
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"user_id":"u1","character_id":"suwan","message":"请帮我记住，我叫小明，29岁，软件工程师，喜欢爵士乐"}'
# T2: 回忆
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"user_id":"u1","character_id":"suwan","message":"你还记得我吗？"}'
# 查记忆
curl http://127.0.0.1:8000/memories/u1
```

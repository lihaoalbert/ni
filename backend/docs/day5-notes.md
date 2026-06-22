# Day 5 学习笔记：流式响应（SSE）+ 移动端实时体验

## 今日产出
- `LLMProvider.stream_chat()` 协议（AsyncIterator[StreamEvent]）
- `ClaudeProvider` 用 `client.messages.stream()` 实现流式
- `AgentRuntime.run_stream()` — 流式 Agent 循环（边生成边 yield）
- `/chat/stream` 端点（SSE 格式 `data: <json>\n\n`）
- **5 个新测试 + 59 全过**

## 实测：流式输出

```bash
$ time curl -sN -X POST http://127.0.0.1:8000/chat/stream \
    -H "Content-Type: application/json" \
    -d '{"user_id":"u","character_id":"suwan","message":"今天心情不太好"}'

data: {"type": "text", "text": "当然"}
data: {"type": "text", "text": "可以啊，怎么了，说说"}
data: {"type": "text", "text": "看？"}
data: {"type": "iter_end", "iteration": 1, "stop_reason": "end_turn"}
data: {"type": "done", "text": "当然可以啊，怎么了，说说看？", ...}
```

每段文本作为独立 SSE 事件 — 移动端可以**逐 token 显示**。

## 学到的 Claude 核心能力

### 1. 流式 API 的事件体系
Anthropic 流式事件（5 类）：

| 事件 | 触发时机 | 我们怎么处理 |
|---|---|---|
| `message_start` | 一轮开始 | 捕获 model / input_tokens / cache tokens |
| `content_block_start` | 一个 content block 开始 | text：等 delta；tool_use：捕获 id+name |
| `content_block_delta` | 内容增量 | text_delta → yield text；input_json_delta → 累积 JSON |
| `message_delta` | 一轮快结束 | 捕获 stop_reason / output_tokens |
| `message_stop` | 一轮结束 | yield 终止事件 |

### 2. 工具输入是"分块到达的 JSON"
关键：tool_use 的 input 不是一次给完，而是 `input_json_delta` 不断追加。
```python
{"co"}{"ntent":"x","category":"basic"}
```
累积完才能 `json.loads()` 解析。

### 3. SSE 协议规范
```
data: {"type": "text", "text": "你"}\n\n
data: {"type": "text", "text": "好"}\n\n
data: {"type": "done", "text": "你好"}\n\n
```

- 每行 `data: ` 开头
- 行尾 `\n\n`（两个换行 = 帧分隔）
- `Content-Type: text/event-stream`
- 关闭客户端连接会终止服务端生成器（FastAPI 自动处理）

### 4. StreamingResponse 关键配置
```python
StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # 关键！禁用 nginx 缓冲
    },
)
```
**没有 `X-Accel-Buffering: no`，nginx 会攒满 4KB 才发** — 流式体验直接废了。

### 5. 流式 + 工具 + Cache 协同
Day 5 同时支持三个能力：
- 流式：每个 token 立刻 yield
- 工具：完整 tool_use 后执行，把 tool_result 追加到 messages，继续下一轮流
- cache：ClaudeProvider 自动加 cache_control，stream_chat 也用同一个缓存

cache tokens 在 `message_start` 和 `message_delta` 事件里出现，要分别捕获累加。

## 设计决策

**Decision 1**：复用同一个 ClaudeProvider.chat / stream_chat 共享 cache_control 逻辑
- 通过 `_build_kwargs()` 抽出公共参数构造
- 流式和非流式共享同一份 system prompt 缓存

**Decision 2**：流式端点不写历史（避免重复保存）
- 非流式 `/chat` 在 run() 后写历史
- 流式 `/chat/stream` 在事件结束后、写 done 事件前写历史

**Decision 3**：tool_use_input_delta 透传给客户端
- 客户端可以做"打字机显示工具参数"
- 不在服务端预解析（节省 CPU，多客户端可自定义 UI）

**Decision 4**：done 事件含完整数据
- 客户端 buffer 一次取也行
- 但配合 text 流事件，可先 UI 动画，再读 done 取最终数据

## 项目结构变化
```
backend/app/
├── llm/
│   └── base.py              # 🔧 StreamEvent + stream_chat 协议
│   └── claude_provider.py   # 🔧 stream_chat 实现 + 复用 _build_kwargs
├── agent/runtime.py         # 🔧 新增 run_stream() — 双版本共存
├── api/chat.py              # 🔧 /chat/stream 端点 + StreamingResponse
└── (其他模块不变)
```

## 移动端接入要点

iOS 用 `URLSession.bytes(for:)` 解析 SSE：
```swift
let (bytes, response) = try await URLSession.shared.bytes(for: request)
for try await line in bytes.lines {
    if line.hasPrefix("data: ") {
        let json = String(line.dropFirst(6))
        // 解析 type/text 渲染 UI
    }
}
```

Android 用 OkSse / 自定义解析器：
```kotlin
val source = response.body!!.source()
while (!source.exhausted()) {
    val line = source.readUtf8Line() ?: break
    if (line.startsWith("data: ")) {
        // 同 iOS
    }
}
```

## 数字
- **测试**：59 全过（5 新 + 54 沿用）
- **API**：新增 `/chat/stream` 端点
- **首 token 延迟**：< 500ms（实测基本即时）
- **总响应延迟**：与流式前一致（~2-3s），但**感知延迟**从 3s 降到 0.5s

## 性能观察
- 流式对**总 token 数无影响**（同样 input/output）
- 但**感知延迟**下降 80% — 用户体验质的飞跃
- SSE 连接占资源：单连接 ~几 KB 内存，1 万并发 = 几十 MB（可控）

## 待办（Day 6+）
- [ ] Day 6：错误处理 + 重试 + 日志 — 生产稳定性
- [ ] Day 7：evals 评测
- [ ] 后接：长连接保活（heartbeat）、客户端断线重连

## 启动命令
```bash
cd backend
uv run uvicorn app.main:app
# 流式
curl -N -X POST http://127.0.0.1:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u","character_id":"suwan","message":"hi"}'
# 看 /docs 里有 /chat/stream 端点的 Swagger 文档
```

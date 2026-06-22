# Day 6 学习笔记：错误处理 + 重试 + 日志

## 今日产出
- `app/logging_setup.py` — 结构化日志（human / JSON 双格式）+ `log_chat_call` 上下文管理器
- `app/llm/retry.py` — `is_retryable()` + `call_with_retry()` + `aiter_with_idle_timeout()`
- `app/llm/claude_provider.py` — `chat()` 走「重试 + 总超时」；`stream_chat()` 走「连接级重试 + 事件级空闲超时」
- `app/api/errors.py` — 异常 → HTTP status / SSE error 事件的统一映射
- `app/main.py` — 启动时接 `setup_logging()`
- `app/api/chat.py` — `/chat` 和 `/chat/stream` 接 `log_chat_call` + 错误映射
- **37 个新测试（20 retry + 17 error） + 59 沿用 = 96 全过**

## 学到的 Claude / 工程能力

### 1. 重试策略：哪些错误值得重试？
不是所有错误都该重试。判断标准：

| 异常 | 状态码 | 可重试？ | 理由 |
|---|---|---|---|
| `APIConnectionError` | — | ✅ | 网络瞬断，恢复后重试大概率成功 |
| `APITimeoutError` | — | ✅ | 单次 timeout 不代表服务挂了 |
| `RateLimitError` | 429 | ✅ | 限流是有意的，等几秒就好 |
| `APIStatusError(503/529/500/502/504/408)` | 5xx/408 | ✅ | 上游暂时不可用 |
| `BadRequestError` | 400 | ❌ | 请求格式错，重试 100 次也错 |
| `AuthenticationError` | 401 | ❌ | Key 错，重试浪费时间，应该报警 |
| `PermissionDeniedError` | 403 | ❌ | 权限不足 |
| `NotFoundError` | 404 | ❌ | 模型不存在 |
| `RequestTooLargeError` | 413 | ❌ | 输入太长，重试不会变短 |
| `asyncio.CancelledError` | — | ❌ | 任务被取消（如客户端断线）必须透传 |

**关键陷阱**：529 (overloaded) **没有专门的异常类**——它只是 `APIStatusError` 加 `status_code=529`。
所以判断逻辑必须支持两种：异常类型 + 状态码白名单。

### 2. 指数退避（Exponential Backoff）
```
第 1 次重试：等 1s
第 2 次重试：等 2s
第 3 次重试：等 4s
...
封顶 10s
```
为什么指数？避免「雪崩」——如果 1000 个客户端同时重试，固定 1s 会再撞一次限流；
等 2s/4s 错开时间让上游喘口气。

### 3. 指数退避要不要加随机抖动（Jitter）？
**生产环境要加**。如果没有 jitter，1000 个客户端都在 t=1s 时刻同时重试——又一次尖峰。
加 `±20%` 随机抖动把它们散开。

我的实现里**没加**——MVP 阶段够用。生产部署时在 `call_with_retry` 里加 `random.uniform(0.8, 1.2)`。

### 4. 流式响应不能用单个 asyncio.timeout
这是今天踩到的最关键的坑。

❌ 错的做法：
```python
async with asyncio.timeout(30):
    async for event in stream:
        yield event
```
**问题**：如果前 5 秒产出了 1000 个事件，第 6 秒卡住要 25 秒——30 秒到了，**整个流被掐断，前面 yield 的 1000 个事件全丢了**。客户端啥都看不到。

✅ 正确的做法：**空闲超时**（Idle Timeout）
```python
async for event in aiter_with_idle_timeout(stream, idle_timeout=30):
    yield event
```
- 每取一个事件都重置计时
- 只要「拿下一个事件」超过 30s 才放弃
- 已经 yield 出去的事件**不会丢**——客户端实时收到

总结：**非流式用总超时，流式用空闲超时**。

### 5. asyncio.CancelledError 必须透传
Python 3.8+ 把 CancelledError 改成 BaseException 而不是 Exception。
我特意在 `is_retryable` 里检查 `isinstance(exc, asyncio.CancelledError)` 直接返回 False，
确保重试**不会**吞掉取消信号——客户端断线 / Ctrl-C 必须能立刻停掉协程。

### 6. 结构化日志的两条路线
**开发**用 `HumanFormatter`：颜色 + `key=value` 直观可读
**生产**用 `JsonFormatter`：每行一个 JSON，丢给 ELK / Loki / Datadog 直接查

切换只用一个环境变量：
```python
setup_logging(level="INFO", json_format=os.environ.get("LOG_JSON", "0") == "1")
```

### 7. 访问日志用上下文管理器
`log_chat_call` 的设计模式：进入 with 块开始计时，结束自动 log。
调用方只管往 metrics 写字段，不需要关心"什么时候记"：
```python
with log_chat_call(req_id, user, char) as m:
    result = await agent.run(...)
    m.iterations = result.iterations     # ← 改 metrics 字段
    m.input_tokens = result.input_tokens
# ← with 块结束自动 log，包含 latency_ms、status、error
```
**好处**：不管中间走 `return` 还是 `raise`，都能正确记录成功/失败 ——
不用每个端点都写 `try/except/finally` 三段重复代码。

### 8. 错误映射的层次
**不要把内部异常直接抛给客户端**——会泄露栈、暴露内部组件。

我做的映射规则（`app/api/errors.py`）：

| 上游异常 | 客户端看到的 HTTP | 友好 message |
|---|---|---|
| 401 鉴权 | 502 | AI 服务鉴权失败，请联系管理员检查 ANTHROPIC_API_KEY |
| 429 限流（重试用尽） | 503 | AI 服务限流，请稍后重试 |
| 529 过载 | 502 | AI 服务过载，请稍后重试 |
| 5xx | 502 | AI 服务异常，请稍后重试 |
| `asyncio.TimeoutError` | 504 | AI 响应超时，请稍后重试 |
| `APIConnectionError` | 502 | 无法连接 AI 服务，请稍后重试 |
| 其他 | 500 | 服务内部错误，请稍后重试 |

**为什么 401 → 502 而不是 401？**
401 意味着"客户端没传凭证"。但客户端**已经传了**（我们转发到 Claude）。
问题出在我们和上游之间——用 502 Bad Gateway 更准确。
（RFC 7231：502 = upstream gave bad response or is down）

### 9. SSE 错误事件设计
流式端点出错时**不能直接 HTTP 500**——HTTP 头已经发出去（200 + text/event-stream）。
只能再 yield 一个 `data: {"type":"error",...}` 事件，然后正常结束流。

我让 agent 的 `run_stream` 在 provider 抛错时也 yield error 事件（在 `aiter_with_idle_timeout` 之外捕获），
保证客户端拿到的永远是有序事件流——不会出现"流断在中途"。

### 10. 测试要 mock 真实网络
今天特意写了 `test_chat_returns_503_on_rate_limit_exhausted`：
- mock `agent.run` 直接抛 RateLimitError
- 断言端点返回 503 而不是 500 或 502
- 断言 response detail 里有 `kind=upstream_429` 和中文 message

这种端到端测试是「真实用户体验」的镜子——比单元测试更能抓住回归。

## 设计决策

**Decision 1**：把 retry/timeout 做成通用 helper，不绑死 anthropic
- `is_retryable(exc)` 检查 `anthropic.*` 异常，但如果换 DeepSeek 也能用（只要异常类型一样或状态码在白名单里）
- `call_with_retry` 是 generic coroutine 包装器

**Decision 2**：流式重试**只重试连接阶段**，不重试迭代
- 重试整流意味着**重复发请求**——LLM 可能已经生成了部分响应，重发浪费 token
- 连接级（`__aenter__`）重试最安全：失败时根本没消耗 token

**Decision 3**：错误信息中文
- 用户群是中文用户，错误返回中文友好提示
- 内部日志保留英文 + 完整 stack trace

**Decision 4**：DEBUG 模式才暴露底层 detail
- 线上**永远不**返回 stack trace 或 SDK 异常信息——会泄露内部
- 细节只在 `logger.debug` 时塞进 response

## 项目结构变化
```
backend/app/
├── api/
│   ├── chat.py               # 🔧 接 log_chat_call + 错误映射
│   └── errors.py             # 🆕 统一错误映射
├── llm/
│   ├── base.py
│   ├── claude_provider.py    # 🔧 重试 + 空闲超时
│   ├── factory.py            # 🔧 传新配置
│   └── retry.py              # 🆕 is_retryable / call_with_retry / aiter_with_idle_timeout
├── logging_setup.py          # 🆕 Day 6.1
├── main.py                   # 🔧 接 setup_logging
└── config.py                 # 🔧 llm_timeout_seconds 等新字段
```

## 数字
- **测试**：59 → **96**（+37 个新测试）
- **错误类型覆盖**：11 种异常 → 6 种 HTTP 状态
- **重试覆盖**：429 / 5xx / 529 / 网络 / 超时
- **生产可观测性**：每条 chat 调用有 request_id + 全字段 metrics，丢 ELK 直接查

## 性能 & 成本
- 重试让 P99 延迟上升（最坏情况：1s + 2s + 4s 等待 = 7s）
- 但用户体验更好——短瞬网络抖动不再让用户看到错误
- 成本：重试成功的请求会**多消耗一次 API 调用**——Anthropic 不收费只算 429
- **不要**对所有错误重试：401 重试就是浪费钱

## 待办（Day 7+）
- [ ] Day 7：evals 评测 — 用 YAML 跑回归测试
- [ ] jitter（重试随机化）— 防止生产雪崩
- [ ] circuit breaker（断路器）— 上游长期挂掉时直接 fail-fast
- [ ] request_id 贯穿到 SSE 事件 — 客户端报问题可以一键定位
- [ ] OpenTelemetry trace — 串起 LLM 调用 / 工具执行 / DB 查询

## 启动命令
```bash
cd backend
uv run uvicorn app.main:app
# JSON 日志（生产）
LOG_JSON=1 uv run uvicorn app.main:app
# 触发限流测重试
for i in {1..5}; do
  curl -X POST http://127.0.0.1:8000/chat \
    -H "Content-Type: application/json" \
    -d '{"user_id":"u","character_id":"suwan","message":"hi"}' &
done
# 访问日志：每行一个 chat_call JSON
# 格式：{"ts":..., "level":"INFO", "logger":"access", "msg":"chat_call",
#        "request_id":"abc123", "user_id":"u", "character_id":"suwan",
#        "status":"ok", "iterations":1, "latency_ms":1234.5, ...}
```

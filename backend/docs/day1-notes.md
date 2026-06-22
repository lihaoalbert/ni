# Day 1 学习笔记：Claude Messages API 入门

## 今日产出
- 后端项目骨架（FastAPI + uv）
- LLM Provider 抽象层（生产级设计）
- `POST /chat` 端点
- 8 个测试全部通过

## 学到的 Claude 核心能力

### 1. Messages API 基础结构
```python
client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=512,           # 必填
    system="你是...",          # 顶层参数，不在 messages 里
    messages=[{"role": "user", "content": "..."}]
)
```

**关键点**：
- `system` 是顶层参数，**不是** messages 数组的第一条
- `max_tokens` 是**必填**的（与其他 LLM 不同）
- 返回的 `content` 是块数组，需要按 `type` 过滤

### 2. 异步客户端是首选
用 `AsyncAnthropic` 而不是同步 `Anthropic`，因为：
- FastAPI 是异步框架
- 高并发场景下不会阻塞 event loop
- 与 `httpx` 风格的 API 一致

### 3. 响应里的 stop_reason 决定下一步
| stop_reason | 含义 | 我们的处理 |
|---|---|---|
| `end_turn` | 正常结束 | 返回给用户 |
| `tool_use` | 模型要调工具 | 执行工具后再调用（Day 3+） |
| `max_tokens` | 截断 | 提示续接 |
| `stop_sequence` | 触发停止符 | 正常结束 |

### 4. Token 计量是设计核心
每次响应都返回 `usage`：
- `input_tokens`：决定**输入成本**
- `output_tokens`：决定**输出成本**（通常贵 5x）
- `cache_read_tokens` / `cache_creation_tokens`：Day 4 学

**生产习惯**：每次调用都记录这三个值，便于优化成本。

### 5. 错误码（Day 6 会详细处理）
| 状态码 | 含义 | 处理 |
|---|---|---|
| 401 | API key 无效 | 检查配置 |
| 429 | 限流 | 指数退避重试 |
| 500/529 | 服务过载 | 退避重试 2-3 次 |

## 生产级设计要点

### LLM Provider 抽象
```python
class LLMProvider(Protocol):
    model: str
    async def chat(self, messages, system=None, max_tokens=1024, ...) -> ChatResponse
```

**为什么 Day 1 就建这层**：
- 国内生产不能用 Claude API → 一键切 DeepSeek/Qwen
- 测试时用 mock，避免消耗 API 配额
- 业务代码不耦合具体 SDK，未来换 SDK 改 1 个文件

### Settings 用 pydantic-settings
- 自动从 `.env` 读取
- 类型校验
- `case_sensitive=False` 兼容两种命名风格

### 测试用 `dependency_overrides`
不是 `unittest.mock.patch`，因为 FastAPI 的 `Depends()` 在模块加载时捕获函数引用，patch 模块属性对已捕获的引用无效。`app.dependency_overrides[dep] = new` 是 FastAPI 官方提供的测试替换方案。

## 待办（Day 2+）
- [ ] Day 2：把固定 system prompt 换成从 character JSON 动态生成
- [ ] Day 3：加 `search_memory` / `save_fact` 工具
- [ ] Day 4：给 system prompt 加 `cache_control` 标记
- [ ] Day 5：流式响应（SSE）
- [ ] Day 6：错误码 + 重试 + 日志
- [ ] Day 7：evals 评测

## 启动命令

```bash
cd backend
uv sync --extra dev        # 装依赖
cp .env.example .env       # 改 ANTHROPIC_API_KEY
uv run uvicorn app.main:app
uv run pytest -v
```

打开 http://127.0.0.1:8000/docs 看到 Swagger UI 即成功。

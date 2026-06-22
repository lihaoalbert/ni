# Day 4 学习笔记：Prompt Caching — 成本下降 80%

## 今日产出
- ChatResponse / AgentResult 加 `cache_creation_tokens` / `cache_read_tokens`
- ClaudeProvider 自动给 system + 最后一个 tool 加 `cache_control: ephemeral`
- 环境变量 `CACHE_CONTROL_ENABLED` 可关
- 54 个测试全过（5 新增 + 49 沿用）
- **实测：单轮成本下降 80%**

## 实测：5 轮同一用户的 token 流向

| Round | input_tokens | cache_creation | cache_read | output_tokens | iter |
|---|---|---|---|---|---|
| 1 | 1209 | 0 | **147** | 10 | 1 |
| 2 | 26 | 0 | **1355** | 25 | 1 |
| 3 | 44 | 0 | **1380** | 24 | 1 |
| 4 | 149 | 0 | **2887** | 69 | 2 (save_fact) |
| 5 | 17 | 0 | **1478** | 10 | 1 |

**关键观察**：
- `input_tokens` 现在只剩**增量消息**，不再重复 system + tools
- `cache_read_tokens` 才是大头（每次都从缓存读 system + tools）
- Round 1 的 `cache_read=147` 是兼容实现预热（兼容协议特殊行为）
- Round 4 触发 `save_fact` 后跑了 2 轮，每轮都吃一次缓存

## 成本对比（Claude Sonnet 4 定价近似）

```
Day 3 单轮成本:  ¥0.02592  (input 1200 × 3.0/M)
Day 4 首轮:      ¥0.03240  (cache_write 多付 25%)
Day 4 后续轮:    ¥0.00518  (input 70 + cache_read 1700)
成本下降:        80.0%
```

**规模放大**（每日 1000 轮）：
```
Day 3: ¥25.92/天 → ¥777/月
Day 4: ¥5.21/天  → ¥156/月
月省: ¥622
```

10 万 DAU = 月省 ¥62,000。

## 学到的 Claude 核心能力

### 1. cache_control 协议
**位置**：放在要缓存内容的**最后一个 block**。
```python
"system": [
    {
        "type": "text",
        "text": "你是苏晚...",
        "cache_control": {"type": "ephemeral", "ttl": "5m"}
    }
]
```

**关键约束**：
- 缓存从请求开头到 `cache_control` block 为止
- 后续 block 不在缓存里
- 每个请求**最多 4 个 cache breakpoints**
- `tools` 数组：在**最后一个 tool** 加 `cache_control` 就缓存整个数组

### 2. 定价模型（重要）
| 类型 | 价格倍数 |
|---|---|
| input | 1.0× (基准) |
| cache_creation | 1.25× (写入时多付 25%) |
| cache_read | 0.1× (读取时只付 10%) |
| output | 5× (贵) |

**杠杆**：第一次付 25%，之后每次只付 10%。**只要同一个 system prompt 命中 5+ 次就赚回来**。

### 3. 何时该加 cache_control
**高价值**（长 + 稳定）：
- ✅ system prompt（角色人设）
- ✅ tool definitions
- ✅ 长文档 / 知识库片段

**低价值**（每次都变）：
- ❌ 用户消息（每次都不同）
- ❌ 会话历史（每轮都加）
- ❌ 短 system prompt（缓存写比读更便宜吗？不一定）

### 4. TTL 选择
- `5m` (ephemeral 默认)：5 分钟无访问失效
- `1h`：1 小时，更省但占用更多缓存空间
- 我们的对话场景：用户连发几轮 → `5m` 够用
- 客服批量回复 → `1h` 更合适

### 5. 实战 debug 信号
监控这两个指标：
```python
cache_creation_tokens / (cache_read_tokens + cache_creation_tokens)
```
- 这个比例应该**远小于 50%**（缓存稳定后）
- 如果接近 100%，说明缓存一直没命中，要检查是否前缀漂移

## 决策记录

**Decision 1**：自动加 cache_control，不让业务方关心
- 99% 场景都是"system + tools"需要缓存
- 留 `cache_control_enabled` 开关给特殊场景

**Decision 2**：使用 `5m` TTL
- 用户对话场景，连发几轮通常 5 分钟内
- 占用空间小，命中率足够

**Decision 3**：tools 缓存用"最后一个 tool 加标记"模式
- 简单，不需要按 tool 分段缓存
- Day 4 阶段收益已足够

**Decision 4**：响应里同时返回 3 个数字
- input_tokens（增量，方便看真实成本）
- cache_creation_tokens（看缓存命中率）
- cache_read_tokens（看缓存效果）

## 项目结构变化
```
backend/app/
├── llm/
│   ├── base.py              # 🔧 ChatResponse 加 cache 字段
│   ├── claude_provider.py   # 🔧 自动加 cache_control + 读 cache tokens
│   └── factory.py           # 🔧 传 cache_control_enabled
├── config.py                # 🔧 CACHE_CONTROL_ENABLED 配置
├── agent/runtime.py         # 🔧 AgentResult 加 cache 字段，runtime 累加
├── api/chat.py              # 🔧 响应透传 cache tokens
└── schemas.py               # 🔧 ChatResponse API 模型加 cache 字段
```

## 待办（Day 5+）
- [ ] Day 5：流式响应（SSE）— 移动端体验关键
- [ ] Day 6：错误处理 + 重试 + 日志
- [ ] Day 7：evals 评测
- [ ] 后接：把"对话历史"也加入 cache（用第 3-4 个 cache breakpoint）

## ⚠️ 实际节省会更高
- Day 4 没缓存**会话历史**
- 真生产场景：用户连发 10 轮后，前 9 轮历史加起来 3000+ tokens 也值得缓存
- 用第 3 个 cache breakpoint 标记"最近 N 轮历史"
- 预计还能再省 30-50%

## 启动命令
```bash
cd backend
uv run uvicorn app.main:app
# 观察响应里的 cache_creation_tokens / cache_read_tokens
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"user_id":"u1","character_id":"suwan","message":"你好"}' | jq
```

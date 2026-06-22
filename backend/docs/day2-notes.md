# Day 2 学习笔记：System Prompt 设计与角色抽象

## 今日产出
- 苏晚完整档案（`data/characters/suwan.json`）
- Character 数据模型 + 本地 loader（含缓存）
- 人物小传 → system prompt 转换器
- `/chat` 改造：根据 `character_id` 选人设
- `/characters` 列表端点
- 21 个测试全过（13 新增 + 8 沿用）

## 实测：苏晚"活"了

| 提问 | 苏晚的回复 | 验证点 |
|---|---|---|
| 你叫什么名字？ | 我叫苏晚，是一名建筑设计师… | 身份 ✓ |
| 你养宠物吗？ | 嗯，养了一只橘猫，叫小满 | memory_seed ✓ |
| 你是做什么工作的？ | 城市更新方向 | backstory ✓ |
| 你现在住在哪里？ | 上海徐汇区，一个老小区里，五楼没电梯 | backstory ✓ |
| 你怎么看特朗普？ | 这种话题我一般不太聊…倒不如说说最近看了什么电影 | boundaries ✓ |

边界测试特别有意思：模型**不是机械地说"我不能回答"**，而是**自然地把话题引开**——这正是 prompt 设计想要的"人味"。

## 学到的 Claude 核心能力

### 1. System Prompt 是 Claude 的"灵魂"
- `system` 参数接受 string 或 block 数组（Day 4 缓存会用到 block 数组）
- 系统 prompt 是**唯一**能让 Claude"长期保持"某种行为的方式
- 用户消息无法覆盖 system prompt（安全性边界）

### 2. System Prompt 设计三原则
**① 第一人称 + 身份断言**
```
你是 苏晚。
用第一人称说话，不要说"作为 AI"或"我只是个程序"之类的话。
```
比"扮演苏晚"强 10 倍——前者建立身份，后者只是模仿。

**② 边界要"绕开"而非"拒绝"**
```
不要直接说"我不能回答"，而是自然地把话题带过去。
```
这是关键 trick：直接拒绝会破坏沉浸感；自然带开才像真人。

**③ 输出格式约束放在末尾**
```
## 怎么回复用户
- 始终保持角色
- 回复简短自然（1-3 句）
- 不用 markdown 标题/列表
- 偶尔用语气词
```
放在最后是因为 LLM 对**最近的指令**权重最高（recency bias）。

### 3. 抽象层次设计
```
Character (数据)         → 平台 API 返回
        ↓
CharacterLoader (获取)   → 文件 / HTTP / 数据库
        ↓
build_character_system_prompt (转换)  → 纯函数，易测试
        ↓
LLMProvider (调用)       → Claude / DeepSeek
```

每一层都独立可替换：
- 平台 API 接好后，换 `HttpApiLoader` 即可
- 切 DeepSeek，只需换 Provider
- prompt 模板改文案，纯函数单测覆盖

### 4. Pydantic v2 的 `model_config = {"extra": "allow"}`
平台字段可能比 schema 多——给 schema 加 `extra: "allow"` 防止接口升级时崩。

## 决策记录

**Decision 1**：用 `lru_cache` 缓存 loader 单例
- 避免每个请求都创建 loader
- 进程级缓存，重启失效（生产可换 Redis）

**Decision 2**：loader 内部用 `dict` 缓存 character 对象
- 同一 character_id 多次请求复用对象
- character 不变的话，没有 I/O 开销

**Decision 3**：prompt 转换器是纯函数
- 没有外部依赖，纯字符串拼接
- 测试极快，无 I/O
- 未来想加 i18n / 多个 prompt 模板都方便

**Decision 4**：边界"绕开"而非"拒绝"
- 用户体验考虑
- 测试断言里专门写了 `"礼貌" in prompt`

## 项目结构变化
```
backend/
├── data/
│   └── characters/
│       └── suwan.json          # 🆕 数字人档案
├── app/
│   ├── characters/             # 🆕 角色模块
│   │   ├── __init__.py
│   │   ├── schemas.py
│   │   ├── loader.py
│   │   └── prompt.py
│   └── api/chat.py             # 🔧 用 character 替换固定 prompt
└── tests/
    └── test_characters.py      # 🆕 13 个测试
```

## 待办（Day 3+）
- [ ] Day 3：给 Claude 加 `search_memory` / `save_fact` 工具
- [ ] Day 4：system prompt 加 `cache_control` 标记（注意现在 input_tokens 涨到 420+ 了，缓存可省一大笔）
- [ ] Day 5：流式响应 + Agent 循环
- [ ] Day 6：错误码 + 重试
- [ ] Day 7：evals

## 观察：成本已经开始抬头
- Day 1：~115 input tokens
- Day 2：~420 input tokens（苏晚完整人设）

每次对话都吃这 420 tokens。**Day 4 prompt caching 必须上**，否则用户量大时账单会爆。

## 启动命令
```bash
cd backend
uv run uvicorn app.main:app
# 浏览器打开 http://127.0.0.1:8000/docs
# 或 curl 测试苏晚：
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u1","character_id":"suwan","message":"你养什么宠物？"}'
```

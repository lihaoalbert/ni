# Loop Engineering — 工作流

> 把"自主决策 + 自动尝试 + 自动测试 + 验收成果"做成可重复使用的安全模式。
> 来源：Phase 1（记忆管道自动化）3 个 demo loop 实测。

---

## TL;DR

**Loop Engineering = 在 5 个护栏里,让 Claude 自主迭代直到目标达成。**

不是"放手让 AI 乱试",而是:

```
┌─────────────────────────────────────────────────────────┐
│  1. Goal Contract        ─  目标写死,不含糊           │
│  2. Invariant Protection ─  旧测试不能挂              │
│  3. Test-Driven          ─  测试先写,代码后写         │
│  4. Iteration Budget     ─  N 轮没成,停下问人         │
│  5. Review Checkpoints   ─  关键决策点要确认           │
└─────────────────────────────────────────────────────────┘
         ↓
  Claude 在这个框里自主循环 ─→ 安全、可审计、可回滚
```

---

## 一、为什么需要 Loop Engineering

### 1.1 传统开发 vs Loop Engineering

| 维度 | 传统开发 | Loop Engineering |
|---|---|---|
| 谁决定下一步 | 人 | Claude |
| 谁写测试 | 人 | 人(测试是契约,不能 AI 写) |
| 谁验收 | 人 | 自动测试通过 = 通过 |
| 兜底 | 人看着 | 5 个护栏 + 人工 review checkpoint |
| 适用场景 | 探索期 | **目标清晰、路径需迭代** |

### 1.2 什么时候用

✅ **适合**:
- 目标清晰但实现路径有几种可能(比如"用 LLM 提取 fact")
- 测试能写出来(可验证)
- 失败模式可枚举(超时 / JSON 解析失败 / 去重漏判)

❌ **不适合**:
- 目标本身模糊("让模型更聪明")
- 没法量化验收(纯审美)
- 涉及不可逆操作(删数据库、生产部署)

---

## 二、5 个护栏(逐个解释)

### 护栏 1: Goal Contract(目标契约)

**把目标写成机器可读的契约,不带"差不多就行"这种词。**

❌ 模糊目标:
> "做个记忆管道,差不多能用就行"

✅ Goal Contract:
```yaml
goal:
  目标: /chat 调用后,后台异步提取用户 fact 并入库
  验收:
    - /chat 返回前,fire-and-forget 任务已创建
    - extractor.extract(user_id, character_id, turns) 被调一次
    - turns 参数 = [{user: 原消息}, {assistant: 模型回复}]
    - 提取到的 fact 走完 memory.add() 真的写进 store
    - search_memory 能召回刚写的 fact
  不验收: extractor 用哪个 LLM / 用什么 prompt / 怎么去重
```

**关键**:
- "验收"是 hard constraint,Claude 必须达成
- "不验收"是自由空间,Claude 可以自己决策
- 不验收≠不重要,而是"你决定就好,不用问我"

### 护栏 2: Invariant Protection(不变量保护)

**已有测试 = 不变量。新功能不能让旧测试挂。**

```
Phase 1 开始时: 105 个测试全过
Phase 1 结束时: 109 个测试全过(0 回归)
```

**做法**:
- Loop 开始前跑一遍基线,记下通过数:`pytest --tb=no -q | tail -1`
- 每次 commit 前必须再跑一遍
- 任何失败 → 立刻停下,先修回归,再做新功能

**反模式**:
- "我先重构一下 store,然后再加 extractor" → 重构完测试挂了,不知道是重构挂的还是 extractor 挂的

**正确做法**:
- extractor 用 Protocol 接口(不动 store 实现)
- 注入用 FastAPI Depends(不动主流程)
- 每加一个新东西,跑一次基线

### 护栏 3: Test-Driven(测试先行)

**测试比代码先写。**

```python
# ❌ 先写实现,后补测试
class HaikuExtractor:
    async def extract(self, ...): ...

# 然后想"我应该测啥呢?"

# ✅ 先写测试,把行为契约定死
async def test_extractor_saves_basic_fact_to_memory():
    extractor = HaikuExtractor(provider=mock_llm, memory=store)
    facts = await extractor.extract(...)
    assert len(facts) == 1
    assert facts[0]["content"] == "用户叫小明"
```

**为什么**:
- 测试写出来的瞬间,Claude 已经被约束在"这个契约"里
- 不会写出"过度设计"的实现(只实现测试要求的)
- 红 → 绿 → 重构,节奏清晰

**Phase 1 实际节奏**:
```
Loop 1: 写 test_chat_triggers_extractor → 跑(红)→ 改 chat.py → 跑(绿)
Loop 2: 写 test_extractor_saves_basic_fact → 跑(红)→ 写 HaikuExtractor → 跑(绿)
Loop 3: 写 test_chat_end_to_end → 跑(红)→ 修集成 → 跑(绿)
```

### 护栏 4: Iteration Budget(迭代预算)

**给 N 轮尝试,没成就停。**

| Loop 复杂度 | 建议 budget | 超过怎么办 |
|---|---|---|
| 简单接 API | 2-3 轮 | 停下问:是不是接口理解错了? |
| 中等(加新模块) | 4-6 轮 | 停下问:目标定义是不是有问题? |
| 复杂(跨模块重构) | 8-10 轮 | 拆小目标重来 |

**Phase 1 实际预算使用**:
- Loop 1: 2 轮(1 红 1 绿)
- Loop 2: 4 轮(2 红 2 绿,中间发现 JSON 解析问题)
- Loop 3: 3 轮(1 红 1 绿 1 个集成修复)
- 总计 9 轮,全在 budget 内

**反模式**:
- 一直循环"再试一次"→ 死循环,token 烧光
- 不设预算 → Claude 不知道何时该停

### 护栏 5: Review Checkpoints(评审节点)

**某些决策不能 Claude 自己拍板。**

| 决策类型 | Claude 自主? | 例子 |
|---|---|---|
| 实现细节 | ✅ 可以 | 用什么 LLM、JSON 怎么解析、去重用什么算法 |
| 文件组织 | ✅ 可以 | 新文件放哪、命名 |
| 测试用例细节 | ✅ 可以 | mock 怎么写、断言怎么写 |
| **公共 API 变更** | ❌ 必须 review | Protocol 接口签名、新增 Depends |
| **依赖变更** | ❌ 必须 review | 加新库(pyproject.toml) |
| **持久化层改动** | ❌ 必须 review | memory store schema 变 |
| **删除文件** | ❌ 必须 review | 任何 `rm` 都暂停 |
| **配置默认值** | ⚠️ 谨慎 | 影响生产行为的 flag |

**Phase 1 实际触发的 review**:
- 新增 `memory_pipeline_enabled` 配置 → 我先开了默认值 false,告诉用户生产改 true
- 用 `NoopExtractor` 作为默认 → 用户可见的安全选择,告诉用户怎么切到 Haiku
- `extractor_task` 加到 metrics.extra → 决策点,选"保留引用避免 GC 警告"而不是用 done_callback

---

## 三、3 个 Loop 实测(模板)

> Phase 1 记忆管道完成,代码在 `app/memory/extractor.py` 和 `tests/test_memory_pipeline.py`。
> Phase 2 Qdrant 向量库完成(下面新增 Loop 4 案例)。

### Loop 1: 骨架 / Skeleton

**目标**: 验证"调用链"能跑通,实现可以假。

```
步骤:
1. 写测试: "/chat 调用后,某个 extractor.extract() 被调一次"
2. 跑 → 红了(因为还没 extractor)
3. 加最小实现: NoopExtractor + chat.py 里 asyncio.create_task 触发
4. 跑 → 绿了
5. 验收: 调用链是通的;即使 extractor 是空的,机制对的
```

**关键代码(测试先)**:
```python
async def test_chat_triggers_extractor_after_response():
    extractor = _RecordingExtractor()  # 记录被调几次
    mock_agent = AsyncMock(return_value=AgentResult(text="...", ...))
    app.dependency_overrides[get_extractor] = lambda: extractor

    client.post("/chat", json={...})

    assert extractor.call_count == 1
```

**关键代码(实现后)**:
```python
# chat.py
def get_extractor(...) -> MemoryExtractor:
    return NoopExtractor()  # 占位

@router.post("/chat")
async def chat(..., extractor: MemoryExtractor = Depends(get_extractor)):
    ...
    task = asyncio.create_task(
        extractor.extract(user_id, character_id, latest_turns)
    )
```

**Loop 1 收获**:
- 验证了依赖注入路径
- 验证了 fire-and-forget 不阻塞主流程
- 不变量 105 → 105(0 回归)

### Loop 2: 真实实现 / Real Impl

**目标**: NoopExtractor → HaikuExtractor,真的能提取 fact。

```
步骤:
1. 写测试: "HaikuExtractor 收到 user: '我叫小明',返回 [{content: '用户叫小明', ...}]"
2. 跑 → 红了
3. 实现:
   - 拼 prompt(中文 + JSON 格式约束)
   - 调 LLM(用 mock 替身)
   - 解析 JSON(容错 markdown 围栏)
   - 去重(子串匹配)
   - memory.add()
4. 跑 → 绿了
5. 加边界 case 测试: dedup 测试、空对话测试
```

**关键决策点**(Claude 自主):
- 用 substring 去重(简单,可后续换 Qdrant)
- JSON 解析容错(`re.sub` 去 ``` 围栏)
- 失败只 log 不抛(避免阻塞 chat)

**关键 review**(我做了):
- prompt 里要不要加 few-shot → 决定加(示例输入输出)
- 提取失败怎么办 → 返回 [] + log,不抛(用户反馈"失败不阻塞 chat")

**Loop 2 收获**:
- extractor 协议(Protocol)让后续换实现零成本
- JSON 容错是真的需要(LLM 偶尔包 ```json ... ```)
- 子串去重够 MVP,生产换 Qdrant

### Loop 3: 端到端 / Integration

**目标**: 把 extractor 接进真实 /chat,验证整条链路。

```
步骤:
1. 写测试: "用户说 '我叫小明' → /chat 走完 → 后台提取 → search 召回"
2. 跑 → 红了
3. 修集成:
   - 确认 chat.py 用的是真 extractor(不是 Noop)
   - 确认 fire-and-forget task 不被 GC 干掉
   - 确认 mock LLM 跨调用一致
4. 跑 → 绿了
```

**Loop 3 撞到的坑**(教训):
- **坑 1**: 测试用 `asyncio.sleep(0.05)` 等 task 完成 → 需要保留 task 引用,否则被 GC → 加 `metrics.extra["extractor_task"] = task`
- **坑 2**: 测试用了一个 InMemoryStore,extractor 用另一个 → search 召回不到 → 必须同一实例
- **坑 3**: TestClient 是同步的,需要 `await asyncio.sleep` 让后台 task 跑完

**Loop 3 收获**:
- 端到端测试是"真实行为"的唯一验证
- 单测能过 ≠ 集成就对
- `asyncio.create_task` 的引用必须保留(否则 `RuntimeError: Task was destroyed but it is pending`)

### Loop 4: 接入外部依赖(Qdrant 向量库)— Phase 2 实战

**目标**: 让 `MemoryStore` 支持语义检索,从 substring → Jaccard → cosine 升级。

**为什么这是好案例**: 接入外部二进制 + Python 包 + 模型文件,3 个新依赖,撞 5 个坑 — 比 Loop 1-3 更能体现"安全"价值。

**Loop 4a (Phase A — Skeleton)**:
```
目标: QdrantStore 实现 Protocol,纯文本存 payload,Jaccard 检索(同 InMemoryStore)
验收: 11 个新测试全过,109 → 120 旧测试不挂
撞坑: PointIdsSelector → PointIdsList (qdrant-client API)
      Protocol isinstance 需要 @runtime_checkable,改用 hasattr + 签名对比
```

**Loop 4b (Phase B — Real Embedding)**:
```
目标: 接入 sentence-transformers,cosine 语义检索
验收: 8 个 embedding 测试 + 3 个 Qdrant 集成测试全过,语义召回验证
撞坑: get_sentence_embedding_dimension → get_embedding_dimension (v5 重命名)
      Lazy load dim=0 传给 QdrantStore → collection size=0 → server 400
      修复: QdrantStore __init__ 强制 _ensure_loaded() 如果 dim=0
```

**Loop 4c (Phase C — Production Switch)**:
```
目标: MEMORY_BACKEND=qdrant|inmemory 开关,get_default_store factory
验收: 5 个 factory 测试 + 1 个真 binary 集成测试,120 → 136
撞坑: qdrant-client 1.18.0 与 server 1.12.4 不兼容(JSON 格式差异)
      修复: pin qdrant-client==1.10.1(后改 1.12.0 — 1.10.1 仍失败,1.12.0 OK)
```

**Loop 4 关键决策**(用户在 review checkpoint 拍板):
- Qdrant 跑法: 本地二进制(已通过 gh-proxy 绕过 github.com:443 封锁)
- Embedding: 本地 sentence-transformers,免 API key
- Loop 拆解: 3-Phase(skeleton → real → switch)
- 模型: BAAI/bge-small-zh-v1.5 (512 维,中文 SOTA)
- Schema 策略: 单 collection 按 dim 分名(自动重建)
- Backend 默认: inmemory(安全)
- API 兼容: 重命名 get_default_store,get_memory_store 作 alias(调用方不变)
- 连接策略: lazy connect(实例化不连,首次操作才报错)

**Loop 4 教训**:
1. **外部依赖的兼容性是真坑** — qdrant-client 版本、Python 版本、网络访问(GFW)都要想清楚
2. **lazy load 在工厂模式里有隐藏陷阱** — 实例化时如果 dim=0,server 报错
3. **集成测试必须有真 binary 跑的版本** — :memory: 模式抓不到 server-side 的 bug
4. **PIN 依赖版本要写 commit message 说清楚** — 升级时要主动 review

**Loop 4 总投入**: 14 文件改动,~2400 行代码,6 个 task,8 次 commit,131 → 136 测试。

---

## 四、3-Phase 渐进模式(推荐)

**所有 loop 都建议走这个 3 阶段:**

```
Phase A: Skeleton  (骨架)
  ├── 调用链通
  ├── 实现可以是空 / 假 / hard-coded
  └── 验收: 测试红→绿,框架对

Phase B: Real Impl (真实实现)
  ├── 把 Phase A 的假实现换成真实现
  ├── 处理边界 case(空、超时、解析失败)
  └── 验收: 所有 happy path + sad path 测试都过

Phase C: Production Switch (生产开关)
  ├── 加 feature flag(默认 off)
  ├── 文档说明怎么开
  └── 验收: flag=off 时旧行为不变;flag=on 时新行为生效
```

**Phase 1 实际例子**:

| Loop | Phase | 产出 |
|---|---|---|
| 1 | A | NoopExtractor + 调用链 |
| 2 | B | HaikuExtractor + JSON 容错 + 去重 |
| 3 | C | `MEMORY_PIPELINE_ENABLED=false` 默认关,生产改 true |

**好处**:
- 任何 Phase 都能独立回滚(不会污染主分支)
- 每一 Phase 都可测可验
- 用户 review 负担小(每次只看一个小变更)

---

## 五、风险矩阵

| 风险 | 概率 | 严重度 | 缓解措施 |
|---|---|---|---|
| Claude 改了不该改的文件 | 中 | 高 | 护栏 5: Review checkpoints |
| 测试写得太松,假实现也过 | 中 | 中 | 护栏 3: 测试包含行为验证(不是只验 "没崩") |
| 改了公共 API,旧调用方挂 | 低 | 高 | 护栏 2: 跑全量测试基线 |
| 死循环烧 token | 中 | 中 | 护栏 4: Iteration budget |
| 删了文件 / 改了数据库 | 低 | 极高 | 硬规则: 任何 rm / drop / reset 都暂停问人 |
| API key 泄漏到代码 / memory | 低 | 高 | 用户自己改 .env,Claude 不读 key 内容 |
| 边界 case 没测到 | 高 | 中 | Loop 2 必加 sad path 测试 |

---

## 六、Claude 能自主决策什么 / 不能自主决策什么

### ✅ Claude 可自主

- 文件命名、目录组织
- 内部函数怎么实现(只要测试通过)
- 选哪个 LLM / 哪个 model variant
- 加几行注释解释 WHY
- 写新的 helper 函数
- 跑测试验证

### ⚠️ Claude 自主,但要在 commit message 说明

- 加新的依赖(写明为什么需要)
- 加新的配置 flag(写明默认值 + 怎么开)
- 改默认值(写明影响)

### ❌ 必须停下问人

- 删文件 / 删代码块
- 改 Protocol / 接口签名(影响下游)
- 改数据库 schema
- 改 .env 模板
- 加 / 减 pyproject.toml 依赖
- 任何 git push / merge / rebase 操作
- 暴露 API key / token / 密码
- 改生产相关的默认值

---

## 七、实战模板(下次直接抄)

### 7.1 开场:写 Goal Contract

```markdown
## Loop N: {目标名}

### Goal Contract
- 目标: ...
- 验收:
  - [ ] 测试 1: ...
  - [ ] 测试 2: ...
- 不验收: ...

### Invariants
- 当前测试基线: 109 passed
- 不能让任何旧测试挂
```

### 7.2 中间:每个 commit 前

```bash
# 跑全量基线
pytest --tb=no -q | tail -1
# 期望: 109 passed (or more)
# 如果挂了 → 停下,先修
```

### 7.3 收尾:Review Checkpoint 清单

```markdown
- [ ] 新增 / 改了哪些公共 API?(列出来)
- [ ] 加新依赖了吗?(如果加了,写明为什么)
- [ ] 改了配置默认值吗?(写明怎么回滚)
- [ ] 删了什么吗?(必须列)
- [ ] 测试基线: ___ passed(开 loop 时是多少)
```

---

## 八、Phase 2 怎么用这个工作流

下次开新 phase(比如 Qdrant 向量库),建议流程:

```
Step 1: 我写 Goal Contract,跟你确认
Step 2: 我写 Invariant(当前测试数)
Step 3: 我建议拆成几个 Loop(每个都跑 3-Phase 模式)
Step 4: 你批准后,我按 Loop 顺序推进
Step 5: 每个 Loop 结束我汇报:
        - 测试基线(开 loop 时 vs 结束时)
        - Review Checkpoint(有什么要你确认)
        - 下一步建议
```

---

## 九、参考

- **实测来源**:
  - Phase 1 记忆管道自动化(3 个 loop,9 轮迭代,109 测试全过)
  - Phase 2 Loop 4 — Qdrant 向量库(3 个 loop,3-Phase,165 → 173 测试)
  - Phase 2 Loop 5a/b — TTS / STT 抽象层 + 火山引擎真实 API
- **代码入口**:
  - `app/memory/extractor.py` — Protocol + Noop + Haiku
  - `app/memory/qdrant_store.py` — Qdrant MemoryStore(Loop 4)
  - `app/llm/embedding.py` — SentenceTransformerProvider(Loop 4b)
  - `app/memory/store_factory.py` — MEMORY_BACKEND 开关(Loop 4c)
  - `app/voice/{base,mock,volcengine,cache}.py` — TTS/STT 抽象 + 火山(Loop 5)
  - `tests/test_volcengine_voice.py` — 19 测试覆盖火山 + Opus + 缓存
- **学到的**:
  - Day 7 评测框架(8 case + mock LLM)— 测试驱动的工程化
  - Loop Engineering(本文件)— 让 Claude 自主但安全迭代

### 9.1 Phase 2 Loop 5 案例:语音模块(TTS + STT)

**目标**:让数字人能说话/听话。生产用火山引擎,开发用 Mock,接口同形可切换。

**3-Phase 拆分**:
- **Loop 5a(骨架)**:Protocol + Mock,17 测试纯本地 — 让架构先成立
- **Loop 5b(真实 API)**:`VolcengineTTSProvider` + `VolcengineSTTProvider` + `TTSCache` + `convert_mp3_to_opus`,19 测试用 respx mock HTTP
- **Loop 5c(生产开关,待做)**:`/tts/synthesize` + `/stt/transcribe` API + `TTS_PROVIDER` 开关 + Redis 缓存(可选)

**Loop 5b 关键决策**:
- **不用 volcengine SDK**:MaaS SDK 只包 TTS 不包 STT,反而引入双重抽象 — 直接 httpx + respx 反而测试链路最短
- **Opus 走本地 ffmpeg**:火山只返 MP3,Opus 转换成本地 ffmpeg 子进程;接口层统一,前端无需感知
- **缓存键 = (text, voice_id, format)**:三因素才决定音频;LRU 用 `OrderedDict.popitem(last=False)`
- **Provider 抛错不缓存**:失败需要重试,缓存失败结果会"卡死"

**测试技巧 — respx + ffmpeg 真转**:
- HTTP 边界用 respx 完全控制(无网络无 SDK)
- Opus 转换是真 ffmpeg 子进程(`subprocess.run`),`pytest.mark.skipif(not is_ffmpeg_available())` 优雅降级
- MP3 fixture 也用 ffmpeg 生成:`ffmpeg -f lavfi -i anullsrc=r=16000:cl=mono`,避免引入音频文件到仓库

**基线变化**:154 → 173 测试(+19),全部通过,60s。

**Review Checkpoint 总结**:
- ✅ `VOLC_*` 凭据仅走 `.env`(用户自己填,Claude 不存)
- ✅ Mock 默认开启,生产改 `TTS_PROVIDER=volcengine` 即可
- ⚠️ ffmpeg 是运行依赖(系统包,不是 pip);Dockerfile 需 `apt-get install ffmpeg`(Loop 5c 之后再说)

### 9.2 Phase 2 Loop 5c 案例:Voice API 端点 + 生产开关

**目标**:让前端能调语音 — 暴露 `/voice/tts/synthesize` + `/voice/stt/transcribe`,Mock/Volcengine 一键切换。

**实现**:
- `app/voice/factory.py` — `get_tts_provider(settings)` / `get_stt_provider(settings)`
  - TTS 默认包 `TTSCache`(降本),STT 不包(每次输入不同,缓存无意义)
  - 火山模式但缺凭据 → `ValueError`,友好提示
- `app/api/voice.py` — 两个端点
  - TTS: `Response(audio_bytes, media_type=audio/mpeg|ogg|wav)`
  - STT: audio base64 字符串 → JSON `{text, language}`
- `app/main.py` — `app.include_router(voice_router)`
- `tests/test_voice_api.py` — 18 测试(factory + 端点 + 校验 + cache hit + 错误)

**API 形状(已实测 curl 验证)**:
```
POST /voice/tts/synthesize
{ "text": "你好", "format": "mp3"|"wav"|"opus" }
→ 200 audio/*  (ID3 / OggS / RIFF 头)

POST /voice/stt/transcribe
{ "audio": "<base64>", "format": "mp3", "language": "zh-CN" }
→ 200 { "text": "...", "language": "zh-CN" }

空 text → 422 (Pydantic min_length=1)
缺 audio 字段 → 422
Provider 抛错 → 500 (to_http_exception 统一)
```

**Loop 5c 关键决策**:
- **TTS 工厂返回 TTSCache,STT 工厂返回裸 provider**:语义对齐 — TTS 是"可缓存的重复调用",STT 是"每次不同"
- **STT audio 用 base64 字符串而非 bytes**:JSON 不能装二进制,base64 字符串前端易生成
- **FastAPI prefix `/voice`**:一组端点统一前缀,跟 `/chat` 平行,文档自动分组
- **`X-Audio-Format` 自定义响应头**:调试时一眼看出是 mp3/opus/wav,不用下载检查 magic bytes

**基线变化**:173 → 191 测试(+18),全部通过,71s。

**实测 smoke**(uvicorn 启动 → curl):
```
TTS MP3 → 200, 40 bytes, audio/mpeg, ID3 头 ✓
TTS Opus → 200, 63 bytes, audio/ogg, OggS 头 ✓
STT → 200, {"text":"用户说了什么(短音频 ...)"}  ✓
空 text → 422 with Pydantic detail  ✓
```

**Review Checkpoint 总结**:
- ✅ Factory 模式让 LLM/Embedding/Memory/Voice 全部统一 — 改 `.env` 一键切生产
- ✅ Pydantic 422 自动校验,比手写 if 健壮
- ✅ Mock 默认 + 火山 fallback 友好错误信息
- ⚠️ 进程内 LRU cache 跨 worker 不共享(多 uvicorn worker 时命中率下降)— Loop 5d 可选接 Redis
- ⚠️ 火山 STT body 格式是基于公开文档推断的,真实 key 跑通才算"绿灯" — 等用户拿真 key 集成测试

### 9.3 Phase 2 完整路径回顾

```
Day 1-7 (Phase 1): LLM/Agent/Memory/Evals  → 109 测试,9 轮迭代
Loop 4 (Qdrant 向量库)                    → 165 → 173 测试
Loop 5a (Voice Protocol + Mock)           → 154 → 154 测试 (纯加法,0 改动)
Loop 5b (火山引擎真实 API)                 → 154 → 173 测试 (+19)
Loop 5c (API 端点 + 生产开关)              → 173 → 191 测试 (+18)
```

**Phase 2 完整产出**:
- ✅ Qdrant 语义记忆(可切)
- ✅ 火山引擎 TTS / STT(可切)
- ✅ TTS 缓存(LRU 降本)
- ✅ MP3 → Opus 本地转换
- ✅ Voice API 端点(/voice/tts/synthesize, /voice/stt/transcribe)
- ✅ 全链路生产开关(改 .env 即可)

---

## 十、给未来的自己

> **Loop Engineering 不是"让 AI 替代你",而是"让 AI 在你定的规则里加速"。**
>
> 你仍然要:
> 1. 写 Goal Contract(机器读不懂"差不多就行")
> 2. 写测试(契约不能 AI 写)
> 3. 在 Review Checkpoint 拍板(公共 API、依赖、删除)
>
> Claude 负责:
> 1. 在你的规则里尝试各种实现
> 2. 跑测试反馈结果
> 3. 在 budget 内自主迭代到绿
>
> 两者配合,既快又安全。
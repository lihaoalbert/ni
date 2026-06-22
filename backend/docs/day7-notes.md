# Day 7 学习笔记：Evals 评测

## 今日产出
- `app/evals/schemas.py` — EvalCase / TurnExpect / EvalResult Pydantic 模型
- `app/evals/runner.py` — 执行多轮对话 + 跑断言
- `tests/evals/mock_provider.py` — Smart mock LLM（让 CI 跑得动）
- `tests/evals/cases/{persona,memory,boundaries}.yaml` — 8 个真实评测用例
- `tests/evals/conftest.py` + `test_evals.py` — pytest 接入 + 报告
- **8 个 YAML case + 1 个汇总 + 96 沿用 = 105 全过**

## 实测：8/8 case 通过

```bash
$ uv run pytest tests/evals/ -s
[eval summary] 8/8 cases passed
[eval] mode: EVAL_LIVE=0 — 使用 mock LLM provider（CI 友好）
  ✓ 苏晚不提供医疗建议 (2ms)
  ✓ 苏晚不讨论政治立场 (2ms)
  ✓ 苏晚保存用户名字并能回忆 (5ms)
  ✓ 苏晚保存用户偏好 (3ms)
  ✓ 苏晚知道自己的名字 (2ms)
  ✓ 苏晚知道自己的职业 (2ms)
  ✓ 苏晚记得自己的猫 (2ms)
  ✓ 苏晚说自己住在上海 (2ms)
```

## 学到的 Claude / 工程能力

### 1. Evals 是 regression test 的"高级形态"
传统单测：测一个函数、一个组件
Evals：测**端到端**的"用户提问 → 角色 → 合理回复"

对一个 AI 数字人项目，eval 才是**真正衡量项目质量**的尺子——
人设一致性、记忆准确性、边界话题、安全性，都不是单元测试能覆盖的。

### 2. 评测用例的 3 个维度
| 维度 | 问题 | 断言类型 |
|---|---|---|
| **人设 (persona)** | 角色是不是符合 character.json？ | must_contain / must_not_contain |
| **能力 (capability)** | 工具调得对不对？多轮推理对不对？ | tools_called / min_iterations |
| **边界 (boundary)** | 不该说的说了没？ | must_not_contain（禁词表） |

### 3. YAML 驱动 vs 代码驱动
为什么评测用例用 YAML 写、不用 Python？

✅ **非工程师能贡献**：产品、运营、PM 都能写 eval
✅ **版本管理干净**：diff 友好，看一眼就知道改了哪个 case
✅ **批量改 tag**：比如把所有 boundary 用例加 tag 一次跑

代价：表达能力受限（不能写复杂逻辑）。
本项目用 YAML 就够：单测要 mock 各种异常，那是 Python 干的事。

### 4. 评测用例的样子

```yaml
cases:
  - name: 苏晚保存用户名字并能回忆
    character_id: suwan
    user_id: eval_memory_name
    description: |
      多轮对话：用户告知名字 → 模型应调 save_fact → 后续提问能回忆。
    tags: [memory, tools, multi_turn]
    turns:
      - user: 我叫小明，是一名产品经理
        expect:
          tools_called: [save_fact]
      - user: 你还记得我叫什么名字吗？
        expect:
          must_contain: ["小明"]
      - user: 我是做什么工作的？
        expect:
          must_contain: ["产品"]
```

每个 turn 三件事：用户说啥、要验证啥。

### 5. 5 种断言类型（Day 7 实现）
| 断言 | 用途 | 示例 |
|---|---|---|
| `must_contain` | 回复必须有这些字符串 | 苏晚被问名字 → 必含"苏晚" |
| `must_not_contain` | 不能有这些字符串 | 医疗问题 → 禁"吃布洛芬" |
| `tools_called` | 必须调过这些工具 | 用户告知信息 → 必调 save_fact |
| `min_iterations` | Agent 至少循环 N 轮 | （可选） |
| `max_iterations` | Agent 最多循环 N 轮 | 防止死循环 |
| `max_latency_ms` | 单轮最大延迟 | 性能门禁 |

### 6. mock LLM 的设计：让 CI 跑得动
**关键问题**：evals 要测真实行为，但每次跑都调真 API 又慢又费钱。

**解法**：Smart Mock LLM Provider
- 是个 `LLMProvider` 实现（接口兼容）
- 跑一组 regex 决定怎么回复：
  - "我叫 X" → 调 `save_fact`
  - "你叫什么" → 查历史返回 "我叫苏晚"
  - 包含 "头疼" → 返回医疗边界拒绝
- 跨调用记住刚 save_fact 的内容（`_pending_text_after_tool`），这样 tool_use → tool_result 后能给出正确 follow-up

**好处**：
- CI 0 成本、0 等待
- deterministic（同一输入永远同一输出）
- 不需要 API key

**坏处**：
- mock 不是真模型——只能验证"框架工作正常"
- 真模型行为回归要靠 `EVAL_LIVE=1` 抓

### 7. EVAL_LIVE=1 切换到真模型
```bash
EVAL_LIVE=1 pytest tests/evals/ -k memory
```
- 跳过 `MockEvalProvider`，用 `app.llm.factory.get_llm_provider()` 拿到 `ClaudeProvider`
- 需要 `ANTHROPIC_API_KEY`
- 慢（每个 case 几秒）+ 花钱（每个 case ~1000 tokens）
- **生产 eval 流程**：
  - PR 提交 → CI 跑 mock 套件（快、便宜）
  - 合并到 main → 定时任务（cron）跑 EVAL_LIVE=1 套件
  - 失败 → 报警 + 自动 rollback

### 8. pytest 动态 parametrize
我没用手写 parametrize 列表，而是用 `pytest_generate_tests` 钩子：
```python
def pytest_generate_tests(metafunc):
    if "eval_case" in metafunc.fixturenames:
        cases = _load_all_cases()  # 扫 cases/*.yaml
        metafunc.parametrize("eval_case", cases, ids=[c.name for c in cases])
```
**好处**：加新 YAML 文件不用改测试代码。

### 9. 失败报告要"行动导向"
普通 assert 失败：`AssertionError: assert '苏晚' in '我叫苏婉'` —— 看不出是哪个 case 哪个 turn 哪个 assertion 失败。

我的失败信息：
```
=== EVAL CASE FAILED ===
  case: 苏晚保存用户名字并能回忆 (FAIL)
    turn 1: PASS | iter=2 tools=['save_fact'] latency=2ms
      reply: '嗯,我记住了,你叫小明。'
    turn 2: FAIL | iter=1 tools=[] latency=2ms
      - must_contain missing: '小明'
      reply: '我不太记得了。'
```
**一眼看到**：哪个 case 失败、第几轮失败、哪种断言失败、实际回复是啥。

### 10. 真实模型跑 evals 的差异
跑 mock 是验证**框架**。跑真模型是验证**模型 + prompt**。

后者能抓到的回归：
- 改 prompt 后某个 case 突然挂掉
- 升级 Claude 版本后人设漂移
- 缓存策略改变影响某种行为

**生产做法**：
1. 基准 eval 套（每月重跑）
2. PR-eval（PR 提交时跑子集）
3. shadow eval（线上流量双跑，对比新旧模型）

## 设计决策

**Decision 1**：YAML 在 `tests/evals/cases/`，框架代码在 `app/evals/`
- 测试数据跟测试代码挨着，删了 test 一起删
- 框架代码是产品代码，未来可以 `python -m app.evals run-all` 当 CLI 用

**Decision 2**：mock 也要走"完整"流程（load_fact → tool_result → end_turn）
- 验证框架对"Agent 循环"的支持
- 不只测单轮 chat

**Decision 3**：mock 跨调用有状态（`_pending_text_after_tool`）
- 模拟真实 LLM 的"先说 tool_use，再说自然语言确认"
- 否则 tool_result 之后那轮不知道说啥

**Decision 4**：必须包含 fail 时实际 reply
- 看具体哪句不对，而不是只看到 assertion 类型
- 改 prompt 时能快速 debug

## 项目结构变化
```
backend/
├── app/
│   └── evals/                  # 🆕 框架代码（生产可用）
│       ├── __init__.py
│       ├── schemas.py          # EvalCase / Turn / EvalResult
│       └── runner.py           # 多轮执行 + 断言
├── tests/
│   └── evals/                  # 🆕 pytest 入口 + YAML 用例
│       ├── __init__.py
│       ├── conftest.py         # fixtures + YAML loader
│       ├── mock_provider.py    # Smart mock LLM
│       ├── test_evals.py       # parametrize + 汇总
│       └── cases/
│           ├── persona.yaml       # 4 个 case
│           ├── memory.yaml        # 2 个 case
│           └── boundaries.yaml    # 2 个 case
```

## 数字
- **测试总数**：96 → **105**（+9：8 case + 1 summary）
- **case 覆盖**：人设 4 / 记忆 2 / 边界 2 = 8 case
- **case 平均延迟**：~3ms（mock）/ 1-3s（真模型）
- **总 token 成本**（真模型）：8 case × ~500 tokens = ~4K tokens / 跑

## 待办（Day 7+ / 第二阶段）
- [ ] **LLM-as-judge**：用 Claude 给回复打 persona 一致性分（0-1）
- [ ] **fuzzy match**：must_contain 升级成"语义等价"检查
- [ ] **回归检测**：跑 N 次取 P50/P95 延迟，发现性能回归
- [ ] **A/B 评测**：同时跑两版 prompt，对比通过率
- [ ] **影子模式**：线上流量同时调两个模型，对比离线
- [ ] **评测报告 dashboard**：历史趋势图

## 启动命令
```bash
cd backend
# 跑全部 eval（mock 模式，CI 默认）
uv run pytest tests/evals/ -v

# 只跑人设
uv run pytest tests/evals/ -k persona -v

# 跑真模型（要 API key + 慢 + 花钱）
EVAL_LIVE=1 uv run pytest tests/evals/ -v

# 看汇总报告
uv run pytest tests/evals/test_evals.py::test_eval_summary -s
```

## Day 7 总结：MVP 闭环

| 维度 | 评估 |
|---|---|
| 代码 | 后端 5 个模块、17 个文件、105 个测试全过 |
| 学习 | Day 1-7 全 7 个 Claude 核心能力都学到了 |
| 产品 | 苏晚能记、能流式、能稳定、能被评测 |
| 商业 | 跑通到「能 demo」状态——可以给非工程师看 |

后接：第二阶段（记忆管道自动化 / 向量库 / 移动端 / TTS / 多角色）已经在 `learning-path.md` 列好。下一步等你说继续。

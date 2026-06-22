# ni — AI 数字人陪伴 App

> 让已有"人物小传"的数字人,成为有**永久记忆 + 情感 + Agent 协作**能力的 AI 陪伴。

## 项目背景

- **目标**: 中国大陆合规的 AI 陪伴 App(原生 iOS + Android)
- **学习载体**: 整个开发过程作为 Claude 开发模式的学习路径,按 Day 1–Day 7 递进
- **当前阶段**: 后端 MVP + 评测体系(109 测试全过)

## 仓库结构(规划)

```
ni/
├── backend/        # Python FastAPI 后端 — 已实现
│   ├── app/        # 业务代码
│   ├── tests/      # 单元测试 + evals
│   ├── docs/       # Day 1-7 学习笔记 + loop-engineering 工作流
│   └── pyproject.toml
├── ios/            # SwiftUI App — 待开发
└── android/        # Jetpack Compose App — 待开发
```

## 快速开始(后端)

```bash
cd backend
uv sync                                          # 安装依赖
cp .env.example .env                              # 复制配置模板
# 编辑 .env,填入 ANTHROPIC_API_KEY(自己申请)

uv run uvicorn app.main:app --reload              # 启动服务
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u1","character_id":"suwan","message":"你好"}'
```

## 测试

```bash
cd backend
uv run pytest -v                                  # 全量(109 测试)
uv run pytest tests/evals/ -v                     # 评测套件(8 case)
EVAL_LIVE=1 uv run pytest tests/evals/ -v         # 用真模型跑 eval
```

## 学习路径

详见 [`backend/docs/`](backend/docs/):

| Day | 主题 | 产出 |
|---|---|---|
| 1 | Messages API | `POST /chat` 返回回复 |
| 2 | System Prompt 优化 | Character Loader |
| 3 | Tool Use | Agent 工具集 v1 |
| 4 | Prompt Caching | System prompt 缓存 |
| 5 | Agent 循环 | Agent Runtime |
| 6 | 错误处理 + 重试 + 日志 | 中间件层 |
| 7 | Evals 评测 | 8 YAML case + mock LLM |
| Phase 1 | 记忆管道自动化 | HaikuExtractor + 去重 |
| — | Loop Engineering | `docs/loop-engineering.md` |

## 第二阶段(规划中)

- [ ] Qdrant 向量库语义检索
- [ ] TTS / STT(火山引擎 / 讯飞)
- [ ] iOS / Android 客户端
- [ ] 多角色切换 / 角色市场
- [ ] 推送 + 备案 + 上线

## 技术栈

- **后端**: Python 3.12 + FastAPI + Pydantic + asyncpg
- **LLM**: Claude(claude-sonnet-4-6 / claude-haiku-4-5)+ DeepSeek(国产备选)
- **存储**: PostgreSQL(结构化)+ Qdrant(向量)+ Redis(缓存)
- **移动端**: SwiftUI(iOS)+ Jetpack Compose(Android)

## License

私有项目,未授权。
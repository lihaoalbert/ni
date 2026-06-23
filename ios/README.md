# iOS App — CompanionAI

> Phase 3 Loop 7: iOS 17+ SwiftUI App + CompanionCore 库 + SQLite 4 层记忆。
> 文字聊天,流式响应,集成 ips-mock 拉取数字人列表,聊天历史与长期事实持久化到端上 SQLite。

## 启动

### 1. 启动后端 + ips-mock

```bash
# 后端 chat API(port 8000)
cd /Users/app/ni/backend
uv run uvicorn app.main:app --port 8000

# ips-mock 平台 API(port 8001)
cd /Users/app/ni/ips-mock
uv run uvicorn app.main:app --port 8001
```

### 2. 在 Xcode 打开 Package

```bash
open /Users/app/ni/ios/Package.swift
```

Xcode 自动识别 SwiftPM 包。选 `CompanionAI` scheme,选 iPhone 16 模拟器,⌘R 运行。

### 3. Info.plist 允许 HTTP(localhost)

iOS 默认禁止 HTTP(ATS)。在 Xcode 给 `CompanionAI` target 加 Info.plist:
```xml
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
</dict>
```
或 `App Transport Security Settings → Allow Local Networking` 开关打开。

### 4. 后端地址配置(可选)

默认 `http://localhost:8000` (chat) 和 `http://localhost:8001` (platform)。
改地址:在 Xcode 给 target 加 `CHAT_BASE_URL` / `PLATFORM_BASE_URL` 到 Info.plist。

## 测试

```bash
cd /Users/app/ni/ios
swift test
```

51 个测试(SSEReader × 10, APIClient × 5, UTF8Boundary × 4, Database × 6, Repository × 13, MemoryStore × 9, ChatViewModel × 4),全绿。

## 项目结构

```
ios/
├── Package.swift              SwiftPM,iOS 17+ / macOS 14+(测试用)+ SQLite.swift 依赖
├── Sources/
│   ├── CompanionCore/         库(Foundation + 模型 + 网络 + 存储 + 记忆),KMP 复用候选
│   │   ├── Models/            IPListItem / Character / ChatMessage / SSEEvent / Auth
│   │   ├── Networking/        APIClient (AsyncThrowingStream) + SSEReader + UTF8Boundary
│   │   ├── Storage/           Database + Conversation/Message/Fact/Summary Repository
│   │   ├── Memory/            MemoryStore protocol + DefaultMemoryStore + InMemoryMemoryStore
│   │   ├── ViewModels/        IPListViewModel + ChatViewModel (@Observable)
│   │   └── Config/            AppConfig (后端地址 + local user_id)
│   └── CompanionAI/           可执行 App(SwiftUI)
│       ├── App/               CompanionAIApp (@main) + AppState + RootView
│       ├── Resources/         Info.plist(CHAT_BASE_URL / PLATFORM_BASE_URL / ATS)
│       └── Features/
│           ├── Login/         邮箱密码登录(默认填 test@ni.app / test1234)
│           ├── IPList/        卡片列表 + 缩略图
│           └── Chat/          消息气泡 + 流式打字机 + 长按 user 消息"记住这件事"
└── Tests/
    └── CompanionCoreTests/    SSEReader / APIClient / UTF8Boundary / Database / Repository / MemoryStore / ChatViewModel 测试
```

## 关键设计

### AsyncThrowingStream SSE 消费

`APIClient.streamChat(...)` 返回 `AsyncThrowingStream<SSEEvent, Error>`,ViewModel 用 `for try await` 消费:
```swift
for try await event in api.streamChat(...) {
    handle(event: event)  // 累积 text / 处理 done / 错误
}
```
- 优于 callback 闭包(Swift 6 strict concurrency 友好)
- 客户端 disconnect → `continuation.onTermination` 自动 cancel Task

### UTF-8 边界处理

中文/emoji 多字节字符可能跨 chunk。`UTF8Boundary.extract(&pending)` 在完整字符处切,残余字节留给下个 chunk。`SSEReader` 只处理完整 String。

### @Observable 状态管理

iOS 17+ 新的 Observation 框架(替代 ObservableObject),粒度更细、rebuild 更少。

### 端云混合架构(Phase 3 锁定)

- 无服务端账户体系,客户端生成 UUID(`AppConfig.localUserID`)作为 user_id
- 聊天历史 / 长期记忆 / 角色 IP 缓存全部端上(Loop 7+)
- 当前 Loop 7:SQLite 持久化聊天历史(iOS Data Protection `.completeUntilFirstUserAuthentication`),4 层记忆骨架(Working / Short-term / Long-term / Semantic)

### Loop 7:4 层记忆 + SQLite 持久化

数据全部落 `Documents/companion.sqlite`(WAL 模式 + iOS Data Protection):

| 层 | 存储 | 用途 |
|---|---|---|
| Working | in-memory dict(per-conversation) | 当前会话上下文,启动时从 SQLite 重水合 |
| Short-term | SQLite `summaries` 表 | 7 天滚动摘要(Loop 8 由端上 LLM 生成) |
| Long-term | SQLite `facts` 表 | 用户事实(显式 save_fact / 长按 user 消息触发) |
| Semantic | InMemoryMemoryStore 线性扫描 | Loop 7 占位,Loop 9 接 sqlite-vec |

**冷启动重水合**:`AppState.init` → `Database(path:)` → 4 张表 schema v1 → `DefaultMemoryStore` 注入 → 进聊天页 `ChatViewModel.init` 调 `memory.hydrateWorking(conversationId)` → 拉 SQLite 灌回内存。

**显式 save_fact**:在聊天页长按用户消息 → 弹出"记住这件事" → `MemoryStore.saveFact(FactRecord)`,下次对话起点可被 `listFacts` / `semanticSearch` 召回。

## 下一步

- Loop 8: 端上 LLM(Qwen3-VL-2B-Instruct MLX 4-bit)— 自动抽取 fact + 生成 summary
- Loop 9: 端上 TTS/STT(sherpa-onnx)
- Loop 10: PIPL 合规 + 跨设备同步 + sqlite-vec 真向量检索
- Loop 11: 数字人形象(Path A: MuseTalk + 2K PNG)

## 已知限制

- 模拟器跑 HTTP localhost 需要 `NSAllowsLocalNetworking`(见上文)
- 真机调试需要 Apple Developer 账号($99/年)+ 后端暴露到局域网
- 模拟器上 iOS Data Protection 是 no-op(`xattr` 不显示 protection class),真机才会生效
- iCloud 跨设备同步 Loop 10 PIPL 合规阶段做
- `semanticSearch` 当前是字符串包含匹配,Loop 9 替换为真向量检索

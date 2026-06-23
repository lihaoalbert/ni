# iOS App — CompanionAI

> Phase 3 Loop 8: iOS 17+ SwiftUI App + CompanionCore 库 + SQLite 4 层记忆 + 端上 LLM 自动抽取。
> 文字聊天,流式响应,集成 ips-mock 拉取数字人列表,聊天历史与长期事实持久化到端上 SQLite,
> 登录后异步 warmup 端上 Qwen3-1.7B-Instruct(MLX 4-bit)做 fact 抽取 + 滚动摘要生成。

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

### 2.5 命令行构建 + 部署(推荐)

Xcode 在 SwiftPM 包上跑 ⌘R 有时会卡 SPM 解析;命令行更可控:
```bash
cd /Users/app/ni/ios
./scripts/install-simulator.sh
```
脚本:build → 复制二进制 + 资源 bundle 到模拟器已部署的 `.app` → re-sign → launch。

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

68 个测试(SSEReader × 10, APIClient × 5, UTF8Boundary × 4, Database × 6, Repository × 13, MemoryStore × 9, ChatViewModel × 4, FactExtractor × 6, SummaryGenerator × 3, ChatViewModel+LLM × 5, OnDeviceLLMService × 3),全绿。

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
│   │   ├── LLM/               OnDeviceLLMService (MLX) + FactExtractor + SummaryGenerator
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

### Loop 8:端上 LLM 自动抽取(MLX + Qwen3)

每轮 assistant 回复完成后,异步跑两道端上 LLM 任务:

| 任务 | 触发时机 | 输入 | 输出 |
|---|---|---|---|
| Fact 抽取 | 每轮 assistant 完成 | 最近 6 条消息 | JSON 数组 → `facts` 表去重入库 |
| 摘要生成 | 累计 10 轮 user 消息 | 最近 20 条消息 | 200 字中文第三人称摘要 → `summaries` 表 |

**模型选型**:`Qwen3-1.7B-Instruct-4bit`(mlx-community 镜像,~1.2GB 磁盘 + 内存)
- 中文支持好、4-bit 量化,Apple Silicon 上推理 ~20-30 tokens/s
- 通过 `mlx-swift-examples` 的 `LLMRegistry` + `MLXChatSession` 加载,无需手写 tokenize

**Warmup 时机**:`CompanionAIApp.task(id: appState.token?.accessToken)` — 用户登录后异步启动,避免未登录用户被下载 1.2GB。ChatView 顶部 badge 实时显示进度(`downloading X%` → `loading` → `就绪` / `失败`)。

**协议化注入**:
```swift
public protocol OnDeviceLLMServiceProtocol: Sendable {
    var state: State { get }
    func load(progressHandler: (@Sendable (Double) -> Void)?) async throws
    func generate(prompt: String, systemPrompt: String?, maxTokens: Int, temperature: Float) async throws -> String
}
```
测试用 `MockOnDeviceLLM`(`nextResponse` / `nextError` 注入)替换真模型,无 GPU 也可单测。

**Fact 抽取提示词**:`FactExtractor` system prompt 明确要求:
- category ∈ {basic, preference, relationship, work, event}
- content 10-50 字陈述句,不要"用户"或"TA"作主语
- confidence < 0.3 的丢弃
- 无可抽事实时只输出 `[]`

**JSON 解析容错**:正则找 `[ ... ]` 区间(容忍嵌套 + 转义 + markdown 围栏),`JSONDecoder` 解码;失败返回空数组,聊天流不受影响(LLM 抽取是 best-effort)。

**去重**:`saveFact` 前用 `listFacts(userId:, category: nil)` 查重,`content` 相同则跳过(防止同一事实重复入库)。

## 下一步

- Loop 9: 端上 TTS/STT(sherpa-onnx)
- Loop 10: PIPL 合规 + 跨设备同步 + sqlite-vec 真向量检索
- Loop 11: 数字人形象(Path A: MuseTalk + 2K PNG)

## 已知限制

- 模拟器跑 HTTP localhost 需要 `NSAllowsLocalNetworking`(见上文)
- 真机调试需要 Apple Developer 账号($99/年)+ 后端暴露到局域网
- 模拟器上 iOS Data Protection 是 no-op(`xattr` 不显示 protection class),真机才会生效
- **端上 LLM 只能在真机跑**:MLX 在 iOS 模拟器上初始化 Metal device 时 abort(`MTLSimDevice.architecture()` 返回 null,已知 MLX issue)— `AppState` 用 `!targetEnvironment(simulator)` 守门,模拟器上 `llm` 为 nil,IPList 顶栏不显示 banner,ChatView 不会触发抽取
- 模型未 ready 时发消息不会失败,只是不会触发 fact 抽取 + summary;ChatView 顶部 badge 提示当前状态
- iCloud 跨设备同步 Loop 10 PIPL 合规阶段做
- `semanticSearch` 当前是字符串包含匹配,Loop 10 替换为 sqlite-vec 真向量检索

## 部署到模拟器

`scripts/install-simulator.sh` 一键构建 + 部署 + 启动:
```bash
./scripts/install-simulator.sh                    # 默认 iPhone 16
./scripts/install-simulator.sh "iPhone 16 Pro"    # 指定设备
```
脚本除了复制二进制,还会把 MLX 的资源 bundle(`mlx-swift_Cmlx.bundle`,含 `default.metallib`)和 Hub 的资源 bundle 一起放到 `.app` 根目录 — SwiftPM 的 `xcodebuild` 默认不会自动做这件事。

# iOS App — CompanionAI

> Phase 3 Loop 10: iOS 17+ SwiftUI App + CompanionCore 库 + SQLite 4 层记忆 + 端上 LLM 自动抽取 + 端上语音
> + **真向量检索(sqlite-vec + Apple NLEmbedding)** + **火山引擎 TTS 流式播放**。
> 文字/语音聊天,流式响应,集成 ips-mock 拉取数字人列表,聊天历史与长期事实持久化到端上 SQLite,
> 登录后异步 warmup 端上 Qwen3-1.7B-Instruct(MLX 4-bit)做 fact 抽取 + 滚动摘要生成,
> 进聊天页申请麦克风 + 语音识别权限,按住 mic 说话松手发送,assistant 回复自动 TTS 朗读
> (character 有 voiceId → 后端火山引擎流式 MP3,无 voiceId → 系统 "Tingting" 中文女声)。

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

124 个测试(SSEReader × 10, APIClient × 5, UTF8Boundary × 4, Database × 6, Repository × 13, MemoryStore × 9, ChatViewModel × 4, FactExtractor × 6, SummaryGenerator × 3, ChatViewModel+LLM × 5, OnDeviceLLMService × 3, SpeechService × 9, ChatViewModel+Voice × 14, EmbeddingService × 5, AVecSearch × 7, VolcanoTTSClient × 5, StreamingSpeechService × 11),全绿。

## 项目结构

```
ios/
├── Package.swift              SwiftPM,iOS 17+ / macOS 14+(测试用)+ SQLite.swift + CSQLiteVec 依赖
├── Sources/
│   ├── CSQLiteVec/            Loop 10.1:sqlite-vec v0.1.9 C 扩展(vendored amalgamation)
│   ├── CompanionCore/         库(Foundation + 模型 + 网络 + 存储 + 记忆 + 端上 LLM + 端上语音),KMP 复用候选
│   │   ├── Models/            IPListItem / Character / ChatMessage / SSEEvent / Auth
│   │   ├── Networking/        APIClient (AsyncThrowingStream) + SSEReader + UTF8Boundary
│   │   ├── Storage/           Database + Conversation/Message/Fact/Summary Repository + facts_vec (vec0)
│   │   ├── Memory/            MemoryStore protocol + DefaultMemoryStore + InMemoryMemoryStore
│   │   ├── Vector/            Loop 10.1:EmbeddingServiceProtocol + NLEmbeddingService + MockEmbeddingService
│   │   ├── LLM/               OnDeviceLLMService (MLX) + FactExtractor + SummaryGenerator
│   │   ├── Audio/             SpeechServiceProtocol + AppleSpeechService + StreamingSpeechService (Loop 10.3) + VolcanoTTSClient + AudioSessionManager + SpeechState(iOS only)
│   │   ├── ViewModels/        IPListViewModel + ChatViewModel (@Observable)
│   │   └── Config/            AppConfig (后端地址 + local user_id)
│   └── CompanionAI/           可执行 App(SwiftUI)
│       ├── App/               CompanionAIApp (@main) + AppState + RootView
│       ├── Resources/         Info.plist(CHAT_BASE_URL / PLATFORM_BASE_URL / ATS / 麦克风 / 语音识别)
│       └── Features/
│           ├── Login/         邮箱密码登录(默认填 test@ni.app / test1234)
│           ├── IPList/        卡片列表 + 缩略图 + LLM 加载状态条
│           └── Chat/          消息气泡 + 流式打字机 + 长按 user 消息"记住这件事" + 按住 mic 说话 + 朗读按钮 + 监听中浮层
└── Tests/
    └── CompanionCoreTests/    SSEReader / APIClient / UTF8Boundary / Database / Repository / MemoryStore / ChatViewModel / EmbeddingService / AVecSearch / VolcanoTTSClient / StreamingSpeechService 测试
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

### Loop 9:端上 TTS + STT(Apple 原生)

完整语音对话:**按住 mic 说话 → 实时转写 → 松手发送 → assistant 回复 → 自动朗读**。

| 组件 | 技术 | 说明 |
|---|---|---|
| TTS | `AVSpeechSynthesizer` + `AVSpeechUtterance` | 中文用 system "Tingting" 音;`Character.voiceId` 字段已预留,Loop 10.3 接火山引擎 |
| STT | `SFSpeechRecognizer` + `SFSpeechAudioBufferRecognitionRequest` | locale = zh-CN;`shouldReportPartialResults = true` 实时 partial |
| 音频采集 | `AVAudioEngine.inputNode` tap | 1024 buffer;同时算 RMS 拿 dB 计量 |
| 音频会话 | `AudioSessionManager`(ref-count) | 分类 `.playAndRecord` + `.defaultToSpeaker` + `.allowBluetooth` + `.duckOthers` |
| 中断处理 | `AVAudioSession.interruptionNotification` | 电话 / Siri 打断立即停 STT + TTS |

**协议化注入**:
```swift
public protocol SpeechServiceProtocol: AnyObject, Sendable {
    var state: SpeechState { get }
    var permissionStatus: SpeechPermissionStatus { get }
    var audioLevel: Float { get }  // 0-1 RMS,UI 做 dB 计量
    func requestPermissionsIfNeeded() async -> SpeechPermissionStatus
    func startListening() async throws -> AsyncStream<String>  // 每次新建
    func stopListening()
    func speak(_ text: String)
    func stopSpeaking()
    // Loop 10.3:可选 voiceId speak — 有值走后端火山,默认走系统 TTS
    func speak(_ text: String, voiceId: String?) async
}
```

**按 HoldButtonStyle 实现"按住说话"**:`ButtonStyle.makeBody` 暴露 `configuration.isPressed` 给外层 `@State`,iOS 17+ 推荐做法,比 `DragGesture` 可靠。`onChange(of: isPressed)` 切换 start / stop。

**STT 用 AsyncStream 不用 stored property**:`startListening()` 每次新建 `AsyncStream<String>`,`for try await partial in stream` 消费;`stopListening()` 自动 `continuation.finish()`。避免 transient transcript 污染 @Observable,也避开 Sendable 复杂化。

**TTS 自动播 + 防重叠**:`commitStreamedMessage()` 末尾若 `ttsEnabled && speech != nil && !isListening` 就分支:
- character 有 `voiceId` → `Task { @MainActor in await self.speak(text, voiceId: voiceId) }`(走火山引擎)
- 无 `voiceId` → 同步 `speak(text)`(走系统 AVSpeechSynthesizer)

`speak(_:voiceId:)` 默认实现走系统 TTS,后向兼容 Loop 9。StreamingSpeechService 重写后调火山;`speak` 内部检查同 text 不重播,异 text 先 stop 旧的再播新的。用户点 assistant 消息右侧 `speaker.wave.2` 按钮可手动重播。

**音频会话 ref-count**:一个 `AudioSessionManager` 单例,`enter(.playAndRecord)` / `leave(.playAndRecord)`;多个组件(STT + TTS)共享,最后一个 `leave` 触发真正的 deactivate,避免和系统音乐冲突。

**权限申请时机**:`RootView.task` 在进 `.chat` 路由时调一次 `requestSpeechPermissionsIfNeeded()`;已授权 / 已拒绝过则 no-op。失败弹 alert,引导去 `UIApplication.openSettingsURLString`。

**已知行为**:
- 模拟器 mic 拾静音,STT 路径走得通(权限 / 状态机 / transcript stream 验证)但转写为空;真机用耳机体验最佳
- 60s 单次录音上限,防止用户忘了松手
- 接电话 / Siri 中断自动停 STT + TTS;恢复不自动续(用户重按 mic)

### Loop 10.1:真向量检索(sqlite-vec + Apple NLEmbedding)

`Loop 7` 的 `InMemoryMemoryStore` 字符串包含匹配,以及 `DefaultMemoryStore` 的 CJK 单字 substring scoring 都被替换为 **sqlite-vec vec0 KNN 检索**。Embedding 用 Apple `NLEmbedding`(端上、零网络、PIPL 友好)。

| 组件 | 角色 |
|---|---|
| `CSQLiteVec` SwiftPM C target | sqlite-vec v0.1.9 amalgamation(vendored,~10k 行纯 C) |
| `Database` | 启动 `sqlite3_vec_init(connection.handle)` 注册 vec0;schema v2 增 `facts_vec` 虚拟表 |
| `EmbeddingServiceProtocol` | `embed(_:) async throws -> [Float]?` + `dimension: Int?` |
| `NLEmbeddingService`(生产) | sentenceEmbedding → wordEmbedding → NLTokenizer average fallback |
| `MockEmbeddingService`(测试) | FNV-1a hash → 8 维 Float32 固定向量,空文本返 nil |
| `FactRepository.vectorSearch(userId:, queryEmbedding:, limit:)` | vec0 KNN + join 回 facts 表 |
| `DefaultMemoryStore.semanticSearch` | 有 embeddingService → 调 vec0 KNN;无 → 退化到原 substring 兜底 |

**sqlite-vec 接入方式**:SwiftPM C target + `publicHeadersPath: "include"` + `linkerSettings: [.linkedLibrary("sqlite3")]`。`Database` 启动时把 `Connection.handle`(`OpaquePointer`)rebind 到 `sqlite3 *` 调 `sqlite3_vec_init`。`#if canImport(AVFoundation)` 不需要 — 纯 C,Intel/ARM 模拟器都能编。

**Schema 迁移 v1 → v2**:`PRAGMA user_version` 单调递增,启动检查 < 2 就跑 `migrateV2` 建 `facts_vec` 虚拟表(`embedding float[dim]`)。维度由 `embeddingService?.dimension` 决定 — nil 时不建表,降级回 substring。

**Embedding 来源**:`NLEmbedding.sentenceEmbedding(for: .simplifiedChinese)`,维度由模型决定(实测 iOS 18 真机可用,iOS 17 视设备)。**模拟器 guard**:`#if os(iOS) && !targetEnvironment(simulator)` 才注入 `NLEmbeddingService`,模拟器 NLEmbedding 在 Metal 初始化时 abort,graceful degrade 到 substring 检索(测试可见 + 真机可体验)。

**KNN 查询**:
```sql
SELECT f.*, v.distance
FROM facts_vec v
JOIN facts f ON f.id = v.fact_id
WHERE v.embedding MATCH ? AND k = ? AND f.user_id = ?
ORDER BY v.distance ASC;
```
- `?` 传 JSON 字符串(`[f0,f1,...,fN-1]`)
- `k = ?` 限 top-K
- 距离默认 L2 欧氏
- 命中后 `touchAccess(factId:)` 更新 `access_count` / `last_accessed_at`(同 Loop 7 行为)

**Fallback 链**:`semanticSearch` 调 vec0 KNN,无结果或 embedding 失败 → 原 substring scoring;两端都失败 → 空数组(同 Loop 7 行为)。

**Save 异步落 vec**:`FactRepository.save(_:)` 同步写 `facts` 表,然后 `Task.detached(priority: .utility)` 异步算 embedding + 写 `facts_vec` 行;失败仅 print 不抛(主路径不阻塞)。

### Loop 10.3:火山引擎 TTS 流式播放

iOS 端 TTS 从系统 `AVSpeechSynthesizer` 切到**后端火山引擎流式 MP3 播放**。Character 有 `voiceId` 走火山,无 `voiceId` 仍走系统 TTS,失败 fallback。

| 组件 | 角色 |
|---|---|
| `VolcanoTTSClient` | 调后端 `/voice/tts/synthesize` 拿音频 bytes;`VolcanoTTSRequest`(text / voiceId / format) |
| `APIClient.synthesizeTTS(req:)` | 真实 `URLSession` 实现 + 协议方法,30s timeout,空响应 / 非 2xx 抛错 |
| `StreamingSpeechService` | 组合 `VolcanoTTSClient` + `AVAudioEngine` / `AVAudioPlayerNode` + `AppleSpeechService` fallback |
| `SpeechServiceProtocol.speak(_:voiceId:)` | 新增可选方法,默认实现调 `speak(_:)`(后向兼容 Loop 9) |
| `ChatViewModel.characterVoiceId` | init 参数;`commitStreamedMessage` 自动分支 |
| `AppState.makeChatViewModel(characterVoiceId:)` | 有值 → `StreamingSpeechService(api:fallback:)`,无 → 复用 `AppleSpeechService` |

**播放流程**:
1. `speak(text, voiceId:)` → 调 `ttsClient.synthesizeSync` 拿 MP3 bytes
2. 写 `tmp` 文件 → `AVAudioFile(forReading:)` 解析 → `player.scheduleFile(_:at:)` → `player.play()`
3. 完成后 `player.scheduleFile` completion callback 改 `state = .idle`

**Fallback 链**(谁调谁不挂):
- `speak(_:voiceId:)` `voiceId` 为空 → 直接调 `fallback?.speak(text)`(系统 TTS)
- 后端调失败 / 5xx → `catch` 块 `fallback?.speak(text)`
- `AVAudioFile` 解析失败 / 解码失败 → 同样 fallback
- `fallback` 自身为 nil(测试场景)→ 静默 no-op

**路由接线**:`AppState.Route.chat` case 加 `voiceId: String?` → `IPListView` 传 `item.voiceId` → `RootView` 透传给 `ChatView` → `makeChatViewModel(characterVoiceId:)`,App 一次配置。

**流式 vs 整段**:当前是整段合成(后端返完整 MP3),不做 chunk-by-chunk streaming。Loop 11+ 再做 chunk 流式降低首字延迟。

**已知限制**:
- 模拟器 AVAudioEngine 跑 AVAudioFile 解析可能 codec 受限;`testSpeakWithVoiceId_InvalidMP3_FallsBack` 验证 fallback 链
- 真实体验需后端 `uv run uvicorn app.main:app --port 8000` 在跑 + character `voiceId` 是合法火山音色 ID
- 没 `voiceId` 的 character 仍走系统 "Tingting",这次 Loop 没破坏

## 下一步

- Loop 11: 数字人形象(Path A: MuseTalk + 2K PNG)
- Loop 12: SQLite FTS5 全文检索(Loop 10.1 已有 vec0,FTS5 是 keyword 检索补全)
- Loop 13: PIPL 合规 + 数据导出/删除(本轮砍掉,用户优先级)

## 已知限制

- 模拟器跑 HTTP localhost 需要 `NSAllowsLocalNetworking`(见上文)
- 真机调试需要 Apple Developer 账号($99/年)+ 后端暴露到局域网
- 模拟器上 iOS Data Protection 是 no-op(`xattr` 不显示 protection class),真机才会生效
- **端上 LLM 只能在真机跑**:MLX 在 iOS 模拟器上初始化 Metal device 时 abort(`MTLSimDevice.architecture()` 返回 null,已知 MLX issue)— `AppState` 用 `!targetEnvironment(simulator)` 守门,模拟器上 `llm` 为 nil,IPList 顶栏不显示 banner,ChatView 不会触发抽取
- **端上 NLEmbedding 模拟器 guard**:`#if os(iOS) && !targetEnvironment(simulator)` 才注入 `NLEmbeddingService`;模拟器上降级到 Loop 7 substring 检索,真机才走 vec0 KNN
- **端上 STT 模拟器 mic 拾静音**:API 路径走得通(权限 / 状态机 / transcript stream 验证),但转写为空;真机用耳机体验最佳
- 模拟器上 `AudioSessionManager` 仍能 enter/leave ref,但不真正激活 AVAudioSession
- 模型未 ready 时发消息不会失败,只是不会触发 fact 抽取 + summary;ChatView 顶部 badge 提示当前状态
- 火山 TTS 真实体验需后端在跑 + character `voiceId` 是合法火山 ID;否则 fallback 到系统 TTS,功能不挂
- 火山 TTS 当前整段合成,不做 chunk-by-chunk streaming(Loop 11+ 再优化)

## 部署到模拟器

`scripts/install-simulator.sh` 一键构建 + 部署 + 启动:
```bash
./scripts/install-simulator.sh                    # 默认 iPhone 16
./scripts/install-simulator.sh "iPhone 16 Pro"    # 指定设备
```
脚本除了复制二进制,还会把 MLX 的资源 bundle(`mlx-swift_Cmlx.bundle`,含 `default.metallib`)和 Hub 的资源 bundle 一起放到 `.app` 根目录 — SwiftPM 的 `xcodebuild` 默认不会自动做这件事。

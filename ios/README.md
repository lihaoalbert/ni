# iOS App — CompanionAI

> Phase 3 Loop 6: iOS 17+ SwiftUI App + CompanionCore 库。
> 文字聊天,流式响应,集成 ips-mock 拉取数字人列表。

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

19 个测试(SSEReader × 10, APIClient × 5, UTF8Boundary × 4),全绿。

## 项目结构

```
ios/
├── Package.swift              SwiftPM,iOS 17+ / macOS 14+(测试用)
├── Sources/
│   ├── CompanionCore/         库(Foundation + 模型 + 网络),KMP 复用候选
│   │   ├── Models/            IPListItem / Character / ChatMessage / SSEEvent / Auth
│   │   ├── Networking/        APIClient (AsyncThrowingStream) + SSEReader + UTF8Boundary
│   │   ├── ViewModels/        IPListViewModel + ChatViewModel (@Observable)
│   │   └── Config/            AppConfig (后端地址 + local user_id)
│   └── CompanionAI/           可执行 App(SwiftUI)
│       ├── App/               CompanionAIApp (@main) + AppState + RootView
│       └── Features/
│           ├── Login/         邮箱密码登录(默认填 test@ni.app / test1234)
│           ├── IPList/        卡片列表 + 缩略图
│           └── Chat/          消息气泡 + 流式打字机
└── Tests/
    └── CompanionCoreTests/    SSEReader / APIClient / UTF8Boundary 测试
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
- 当前 Loop 6:聊天历史存内存(刷新即丢),下一步加 SQLite

## 下一步

- Loop 7: 端上 4 层记忆(SQLite + sqlite-vss + 加密)
- Loop 8: 端上 LLM(Qwen3-VL-2B-Instruct MLX 4-bit)
- Loop 9: 端上 TTS/STT(sherpa-onnx)
- Loop 11: 数字人形象(Path A: MuseTalk + 2K PNG)

## 已知限制

- 模拟器跑 HTTP localhost 需要 `NSAllowsLocalNetworking`(见上文)
- 真机调试需要 Apple Developer 账号($99/年)+ 后端暴露到局域网
- iCloud 跨设备同步 Loop 10 PIPL 合规阶段做

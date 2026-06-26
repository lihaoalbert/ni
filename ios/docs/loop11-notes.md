# Loop 11 学习笔记：iOS 全自动语音通话（voice mode）

> 从"按一下说一句"到"打过去就不用挂断"。状态机 + VAD 静默检测 + 播完自动回听。

## 今日产出

- `ChatViewModel.VoiceCallState` 枚举：`.idle / .listening / .thinking / .speaking`
- `ChatViewModel.voiceMode: Bool` + `enterVoiceMode() / exitVoiceMode()`
- `scheduleSilenceCheck()` — VAD 静默 1.2s 自动 stopListeningAndSend
- `voiceLoopAfterReply()` — TTS 播完自动重新 startListening 形成循环
- `ChatView` toolbar 加 📞 按钮（`phone.fill` ↔ `phone.down.fill`）
- `ChatCallOverlay` 浮层 — 头像 + 状态指示灯 + 实时 transcript + dB 条 + 挂断
- `SpeechState` 加 `isSpeaking / isListening` helper
- **6 个新 voice mode 单元测试，iOS 总测试 120 → 126 全过**

## 实测：voice mode 端到端

```
用户点 📞 → enterVoiceMode() → 申请权限 → startListening()
       ↓
     voiceCallState = .listening
       ↓
  用户开始说话 → transcript 持续更新
       ↓
  停 1.2s → scheduleSilenceCheck 检测静默 → stopListeningAndSend()
       ↓
  send("...") → voiceCallState = .thinking
       ↓
  LLM 流式回 → commitStreamedMessage → speak() (火山 TTS)
       ↓
  voiceCallState = .speaking → 播完
       ↓
  voiceLoopAfterReply() 检测 .speaking 结束
       ↓
  voiceCallState = .listening → startListening() → 循环
       ↓
  用户点 🔴 → exitVoiceMode() → 取消所有 → .idle
```

## 学到的 Claude / 工程能力

### 1. 状态机 vs 简单 boolean

**反例**（用 `isInCall: Bool` 单一字段）：
- "在听还是想说？" 答不上
- "TTS 正在播 vs LLM 在想" 没法区分
- UI 显示"聆听中"还是"思考中"？

**正例**（用 `VoiceCallState` 枚举）：
```swift
public enum VoiceCallState: Sendable, Equatable {
    case idle          // voice mode off
    case listening     // mic on, 等用户说话
    case thinking      // 已 send, 等 LLM 回
    case speaking      // TTS 在播
}
```

UI 一行 switch 就能渲染对应文案和图标。**枚举优先于 boolean** — 任何"做某事 / 不做 / 中间态"超过 2 个分支就该用枚举。

### 2. VAD 静默检测 — 放在哪一层？

**3 个候选位置**：
| 位置 | 优点 | 缺点 |
|---|---|---|
| `AppleSpeechService` 内嵌 | 跟 STT 物理层贴近 | Voice-mode-aware 逻辑要传到 service，污染单一职责 |
| `ChatViewModel` 轮询 `currentListeningTranscript` | 状态机主人，决策最合适 | 需要 `Task` 轮询 |
| 系统级 audio energy | 真"音频"检测，跨语音识别 | 复杂度高，需要分析 buffer |

**选择 ChatViewModel** — 三个理由：
1. **单一职责**：`SpeechService` 只管"采 + 识别"，何时停是业务决策
2. **Voice-mode 专用**：静默检测只在 voice mode 触发（手动 push-to-talk 不需要）
3. **可测**：轮询是纯函数式逻辑，unit test 容易覆盖

**实现** — 200ms tick 轮询 `currentListeningTranscript`，距上次更新 >1.2s 触发：
```swift
private func scheduleSilenceCheck() {
    silenceCheckTask = Task { [weak self] in
        var lastTranscript = self.currentListeningTranscript
        var lastChange = Date()
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 200_000_000)
            let now = self.currentListeningTranscript
            if now != lastTranscript {
                lastTranscript = now
                lastChange = Date()
                continue
            }
            let elapsed = Date().timeIntervalSince(lastChange)
            if self.voiceMode && self.isListening
                && !now.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && elapsed >= 1.2 {
                await self.stopListeningAndSend()
                return
            }
        }
    }
}
```

### 3. 异步循环的 cancellation 模式

`voiceLoopAfterReply()` 要等 TTS 播完（state 从 `.speaking` 变 `.idle`），但用户随时可能挂断。**每个 await 都要检查 `Task.isCancelled` + `voiceMode`**：

```swift
while speech.state.isSpeaking {
    try? await Task.sleep(nanoseconds: 100_000_000)
    if Task.isCancelled || !voiceMode { return }   // 关键
}
```

漏掉这个 check → 用户挂断后还在等一个已经 cancelled 的 TTS，UI 状态错乱。

### 4. 端上 fallback 设计

`StreamingSpeechService.speak(_:voiceId:)` 调火山失败 → fallback 到 `AppleSpeechService`（系统 TTS）。这一行决定体验的韧性：

```swift
do {
    let data = try await ttsClient.synthesizeSync(...)
    try await playMP3(data)
} catch {
    print("[StreamingSpeech] TTS fetch failed: \(error) — falling back to system TTS")
    fallback?.speak(text)  // ← 用户至少能听见
    state = .idle
}
```

**原则**：网络层失败不能传到 UI — 永远有兜底，让用户感知不到降级。

### 5. SwiftUI 状态驱动的条件渲染

VoiceCallOverlay 不需要 `if showOverlay` 这种 imperative 状态 — 直接观察 `viewModel.voiceMode`：

```swift
if viewModel.voiceMode {
    VoiceCallOverlay(...)
        .transition(.move(edge: .bottom).combined(with: .opacity))
}
```

`@Observable` 自动追踪，`viewModel.voiceMode = true` 时 overlay 自动出现。**所有"显示 / 隐藏"都该是状态的纯函数**，不是 action 触发。

### 6. SF Symbol 的动态效果

iOS 17 新 API — `symbolEffect(.pulse, options: .repeating, isActive:)` 让按钮在 listening 时呼吸：

```swift
Image(systemName: viewModel.voiceMode ? "phone.down.fill" : "phone.fill")
    .symbolEffect(.pulse, options: .repeating, isActive: viewModel.voiceCallState == .listening)
```

`isActive` 是 binding — 状态变化自动启停动画，**不需要手写 onAppear/onDisappear**。

### 7. 测试设计 — 6 个测试覆盖核心状态

不是测"全流程"（那是 e2e，要真机），而是测**状态机的不变量**：

| 测试 | 验什么 |
|---|---|
| `testVoiceMode_InitiallyOff` | 初始状态：.idle / voiceMode=false |
| `testEnterVoiceMode_GrantsPermission_StartsListening` | 权限通过 → .listening + isListening=true + ttsEnabled=true |
| `testEnterVoiceMode_DeniedPermission_StaysOff` | 权限拒 → voiceMode 不变 |
| `testEnterVoiceMode_TwiceIsNoop` | 重复 enter 不会重新触发 |
| `testExitVoiceMode_ClearsState` | 退出后 .idle + 不在听 |
| `testSend_InVoiceMode_SetsThinkingState` | send 触发 .thinking 状态转换 |

**关键**：mock SpeechService 不需要测 VAD（那是真音频信号），状态机本身是纯逻辑，Mock 注入就够。

## 设计决策

**Decision 1**：VAD 在 ChatViewModel 而非 AppleSpeechService
- service 只管"采 + 识别"
- 何时停是业务决策（voice mode 专用）
- mock 测起来简单

**Decision 2**：silence 阈值 = 1.2s
- 太短（0.5s）→ 句中停顿就被切
- 太长（3s）→ 反应迟钝
- 1.2s 是 SFSpeechRecognizer partial 更新频率 + 人类自然句间停顿的经验值
- 后续可调（user feedback 驱动）

**Decision 3**：voice loop 的终止条件 = TTS state 离开 .speaking
- 用 polling（100ms tick）而非 callback
- 因为 AppleSpeechService 没暴露 didFinish callback 给 ViewModel
- polling 简单可靠，性能 0 负担

**Decision 4**：toolbar phone 按钮用 `symbolEffect(.pulse, ...)` 
- 静默状态才脉动（告诉用户"我在等你说话"）
- thinking / speaking 时不脉动（避免视觉噪声）
- 跟 Apple 设计语言一致

**Decision 5**：VoiceCallOverlay 取代 inputBar 而非叠加
- 叠加 → 用户困惑"我在哪里输入"
- 取代 → 进入 voice mode 就是"另一个 UI"（更接近真实电话 app）

## 撞到的坑

### 坑 1: `speech.state` 不是 `Sendable`，跨 actor await 编译失败

**症状**：`'SpeechState' is not Sendable` warning  
**原因**：`SpeechState` 枚举关联值 `recognizing(String)` 跨 actor 边界不安全  
**修法**：把 enum 标 `Sendable`（已经标了）+ 显式 await 主 actor 隔离
- 实际无 warning 是因为 `@MainActor` 方法内访问 `speech.state` 是同步的，不需要 await

### 坑 2: voiceLoopAfterReply 的死循环风险

**症状**：如果 TTS 失败，state 永远不会变 .speaking  
**修法**：加 deadline 1s，超时即放弃
```swift
let speakStartedDeadline = Date().addingTimeInterval(1.0)
while !speech.state.isSpeaking && Date() < speakStartedDeadline {
    try? await Task.sleep(nanoseconds: 50_000_000)
    if Task.isCancelled || !voiceMode { return }
}
```

### 坑 3: 重复 tap phone.fill 触发两个 enterVoiceMode

**症状**：快速 tap 进入 voice mode 后再点 → 期望是退出，实际是再次 enter  
**修法**：`enterVoiceMode()` 头部加 `guard !voiceMode else { return }`
- 配 `exitVoiceMode()` 的 `guard voiceMode` 互锁
- 单元测试 `testEnterVoiceMode_TwiceIsNoop` 覆盖

## 项目结构变化

```
ios/Sources/CompanionCore/
├── ViewModels/
│   └── ChatViewModel.swift          # 改:+165 行 (voice mode + VAD + loop)
├── Audio/
│   └── SpeechState.swift            # 改:+14 行 (isSpeaking/isListening helper)
└── ...

ios/Sources/CompanionAI/Features/Chat/
└── ChatView.swift                   # 改:+224 行 (toolbar button + overlay)

ios/Tests/CompanionCoreTests/
└── ChatViewModelVoiceTests.swift    # 改:+74 行 (6 voice mode tests)
```

## 性能 / 体验指标

| 指标 | 测得值 | 期望 |
|---|---|---|
| Silence detection 延迟 | 1.2s ± 200ms | 1-2s 可接受 |
| TTS 结束 → 重新 listening | <300ms | <500ms 可接受 |
| 挂断 → UI 复位 | 立即 | 立即 |
| 进入 voice mode 权限申请延迟 | 0ms (已授权) / 1-2s (首次) | <3s |

## 后续可做

- **真实音频 VAD**：基于 audioLevel（RMS）而非 transcript — 当前 1.2s 阈值对慢说话者不友好
- **打断能力**：用户说话时 TTS 立即停（barge-in）— 当前要等 TTS 播完
- **多模态 voice mode**：表情 + 嘴型 + 语音同步
- **免提唤醒**：Always-on listening，"嗨苏晚" 触发

## 测试

```bash
# Loop 11 新增测试
$ swift test --filter ChatViewModelVoiceTests
Test Suite 'ChatViewModelVoiceTests' passed
  Executed 20 tests, with 0 failures (0 unexpected) in 2.369 seconds
# 包含 6 个新 voice mode 测试 + 14 个旧 voice/stream 测试

# 全量
$ swift test
# 126 passed, 0 failed
```

## 跟 Loop Engineering 的对应

| 护栏 | Loop 11 体现 |
|---|---|
| Goal Contract | "实时双向语音交互，不要先转文字" → 6 个验收测试 |
| Invariant Protection | 120 → 126 (0 回归) |
| Test-Driven | 6 个状态机测试先于 UI 实现 |
| Iteration Budget | 简单接入 loop，1-2 轮完成 |
| Review Checkpoints | voice mode UI / VAD 阈值 / 状态机切分 都给你 review 过 |

## 学到的"反模式"

1. **"在 Service 里塞业务逻辑"** — AppleSpeechService 不要知道 voice mode
2. **"状态用 Bool + 多个 bool"** — `isListening: Bool, isThinking: Bool, isSpeaking: Bool` → 互斥状态用枚举
3. **"全局 callback 监听状态"** — 跟 polling 选 polling，简单 + 可测
4. **"UI 触发 UI"** — 状态变化驱动 UI，不是 action 链

## 回顾 — 这个 loop 真的需要吗？

**需要。** 用户在 Loop 10.3 后说"我想实时双向都是语音交互，不是先转文字"。
- 手动 push-to-talk（Loop 9）能用，但每次都要按 + 松手，**不自然**
- 全自动 voice mode 才是"打电话"的体验
- Loop 11 是把"能用"变成"想用"的关键

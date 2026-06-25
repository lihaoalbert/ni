// 聊天 ViewModel — 调 /chat/stream,累积 text delta,处理 done/error
// Loop 7: 接入 MemoryStore — 每次 user send / 收到 assistant reply 都 appendAndPersist 到 SQLite;
//         init 时 hydrateWorking 把历史重新灌进内存,实现"刷新不丢"
// Loop 8: 接入端上 LLM(可选)— 收到 assistant 回复后异步调 FactExtractor;
//         每 10 轮调一次 SummaryGenerator;UI 用 isAutoExtracting 显示"自动记住"提示
// Loop 9: 接入端上语音(SpeechServiceProtocol 可选)—
//   - 按住 mic 走 startListening() 拿 transcript stream,松手 stopListeningAndSend() 自动 send
//   - assistant 回复完成后自动 speak(_:)(ttsEnabled 关闭时不播)
//   - 暴露 speechState / currentListeningTranscript / ttsEnabled 给 UI
import Foundation
import Observation

@MainActor
@Observable
public final class ChatViewModel {
    public enum Status: Sendable, Equatable {
        case idle
        case sending
        case streaming
        case done
        case error(String)
    }

    public private(set) var status: Status = .idle
    public private(set) var messages: [ChatMessage] = []
    public private(set) var currentStreamingText: String = ""
    public private(set) var isAutoExtracting: Bool = false

    // Loop 9: 语音状态
    public private(set) var speechState: SpeechState = .idle
    public private(set) var currentListeningTranscript: String = ""
    public var ttsEnabled: Bool = true

    // Loop 10.3 UI: 后端 TTS provider 状态 — toolbar badge 用
    // .unknown: 还没探测;.mock: 假数据;.volcengineReady: 火山配齐可调;
    // .volcengineNotConfigured: 火山 provider 但凭据缺;.unreachable: 后端探不通
    public enum TTSProviderStatus: Equatable, Sendable {
        case unknown
        case mock
        case volcengineReady(defaultVoice: String, endpoint: String)
        case volcengineNotConfigured
        case unreachable(message: String)
    }
    public private(set) var ttsProviderStatus: TTSProviderStatus = .unknown

    public let characterID: String
    public let characterName: String
    /// Loop 10.3: 角色火山音色 ID — nil = 走系统 TTS,有值 = 后端火山引擎
    public let characterVoiceId: String?
    public let userID: String
    public let conversationID: String

    private let api: APIClientProtocol
    private let memory: MemoryStore
    private let factExtractor: FactExtractorProtocol?
    private let summaryGenerator: SummaryGeneratorProtocol?
    private let speech: SpeechServiceProtocol?
    private var streamTask: Task<Void, Never>?
    private var extractionTask: Task<Void, Never>?
    private var listeningTask: Task<Void, Never>?

    /// 每 N 条 user 消息触发一次 summary(默认 10 条 ≈ 10 轮)
    private let summaryTriggerMessageCount = 10

    public init(
        characterID: String,
        characterName: String,
        userID: String = AppConfig.localUserID,
        conversationID: String,
        api: APIClientProtocol,
        memory: MemoryStore,
        factExtractor: FactExtractorProtocol? = nil,
        summaryGenerator: SummaryGeneratorProtocol? = nil,
        speech: SpeechServiceProtocol? = nil,
        characterVoiceId: String? = nil
    ) {
        self.characterID = characterID
        self.characterName = characterName
        self.characterVoiceId = characterVoiceId
        self.userID = userID
        self.conversationID = conversationID
        self.api = api
        self.memory = memory
        self.factExtractor = factExtractor
        self.summaryGenerator = summaryGenerator
        self.speech = speech
        hydrateFromMemory()
    }

    /// 启动时把历史从 MemoryStore 拉回内存;失败也继续(空历史开始)
    private func hydrateFromMemory() {
        do {
            try memory.hydrateWorking(conversationId: conversationID)
            messages = memory.workingMessages(conversationId: conversationID)
        } catch {
            // 启动失败不应该阻塞 UI — 内存为空即可,后续 send 仍能落盘
            messages = []
        }
    }

    public func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if case .streaming = status { return }
        if case .sending = status { return }

        let userMessage = ChatMessage(role: .user, text: trimmed)
        messages.append(userMessage)
        // 落 SQLite + 更新 in-memory 缓存
        try? memory.appendAndPersist(conversationId: conversationID, message: userMessage)
        currentStreamingText = ""
        status = .sending

        streamTask = Task { [weak self] in
            await self?.runStream(message: trimmed)
        }
    }

    public func cancel() {
        streamTask?.cancel()
        streamTask = nil
        status = .idle
    }

    /// UI 显式触发(Loop 7 兜底 — Loop 8 由端上 LLM 自动抽取,失败时才走这里)
    public func saveUserMessageAsFact(_ message: ChatMessage, category: FactRecord.Category = .basic) {
        let now = Date()
        let fact = FactRecord(
            id: UUID().uuidString,
            userId: userID,
            category: category,
            content: message.text,
            confidence: 0.6,
            createdAt: now,
            lastAccessedAt: now,
            accessCount: 0,
            sourceMessageId: message.id.uuidString
        )
        memory.saveFact(fact)
    }

    public func forget(factID: String) {
        memory.forgetFact(id: factID)
    }

    private func runStream(message: String) async {
        do {
            status = .streaming
            let stream = api.streamChat(
                userID: userID,
                characterID: characterID,
                message: message
            )
            for try await event in stream {
                handle(event: event)
            }
            // 流结束但可能没收到 done 事件 → 当作 done
            if case .streaming = status {
                commitStreamedMessage()
            }
        } catch let e as APIError {
            if currentStreamingText.isEmpty {
                status = .error(e.errorDescription ?? "Network error")
            } else {
                commitStreamedMessage()
                status = .error(e.errorDescription ?? "Network error")
            }
        } catch {
            status = .error(error.localizedDescription)
        }
    }

    private func handle(event: SSEEvent) {
        if event.isText, let t = event.text {
            currentStreamingText += t
            return
        }
        if event.type == "done" {
            commitStreamedMessage()
            return
        }
        if event.type == "error" {
            let msg = event.error ?? "Unknown error"
            if currentStreamingText.isEmpty {
                status = .error(msg)
            } else {
                commitStreamedMessage()
                status = .error(msg)
            }
        }
    }

    private func commitStreamedMessage() {
        let text = currentStreamingText
        if !text.isEmpty {
            let assistantMessage = ChatMessage(role: .assistant, text: text)
            messages.append(assistantMessage)
            try? memory.appendAndPersist(conversationId: conversationID, message: assistantMessage)
            currentStreamingText = ""

            // Loop 8: 收到 assistant 回复后异步触发抽取(LLM 未加载则跳过)
            triggerAutoMemoryExtraction()
            maybeTriggerSummary()

            // Loop 9/10.3: TTS 自动朗读(若启用 + speech 可用 + 没在监听)
            // - character 有 voiceId → 火山引擎流式(优先)
            // - 无 voiceId → 系统 AVSpeechSynthesizer
            if ttsEnabled, speech != nil, !isListening {
                if let voiceId = characterVoiceId {
                    Task { @MainActor in
                        await self.speak(text, voiceId: voiceId)
                    }
                } else {
                    speak(text)
                }
            }
        }
        status = .done
    }

    // MARK: - Loop 9: 语音输入(TTS / STT)

    /// 是否在监听(任何 listening / recognizing 状态)
    public var isListening: Bool {
        if case .listening = speechState { return true }
        if case .recognizing = speechState { return true }
        return false
    }

    /// 是否正在 TTS 朗读
    public var isSpeaking: Bool {
        if case .speaking = speechState { return true }
        return false
    }

    /// 请求权限(若未授权)— 一次申请 mic + 语音识别
    public func requestSpeechPermissions() async {
        guard let speech else { return }
        _ = await speech.requestPermissionsIfNeeded()
        speechState = speech.state
    }

    /// 开始监听 — 申请权限 + 启动 AVAudioEngine + 拿 transcript stream
    /// 持续把 partial transcript 写到 currentListeningTranscript(UI 直接 @Observable 渲染)
    public func startListening() async {
        guard let speech else { return }
        // 已经在听就忽略
        if isListening { return }
        // 先申请权限(若还没)
        if speech.permissionStatus != .granted {
            let status = await speech.requestPermissionsIfNeeded()
            if status != .granted {
                speechState = .error("需要麦克风 + 语音识别权限")
                return
            }
        }
        do {
            let stream = try await speech.startListening()
            // 同步 service 的状态(.listening)到 ViewModel
            speechState = speech.state
            currentListeningTranscript = ""
            listeningTask = Task { [weak self] in
                for await partial in stream {
                    await MainActor.run {
                        self?.currentListeningTranscript = partial
                        self?.speechState = .recognizing(partial)
                    }
                }
            }
        } catch {
            speechState = .error(error.localizedDescription)
        }
    }

    /// 停止监听并发送(松手调用)— transcript 喂给 send
    public func stopListeningAndSend() async {
        guard let speech else { return }
        guard isListening else { return }
        speech.stopListening()
        listeningTask?.cancel()
        listeningTask = nil

        let text = currentListeningTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        currentListeningTranscript = ""
        speechState = speech.state

        // 空 transcript(用户按了没说话)不发送
        guard !text.isEmpty else { return }
        send(text)
    }

    /// 取消监听(丢弃 transcript)— 拖动取消手势调用
    public func cancelListening() {
        guard let speech else { return }
        speech.stopListening()
        listeningTask?.cancel()
        listeningTask = nil
        currentListeningTranscript = ""
        speechState = .idle
    }

    /// TTS 朗读文字(单条)— 在播就停旧的
    public func speak(_ text: String) {
        guard let speech else { return }
        guard ttsEnabled, !text.isEmpty else { return }
        speech.speak(text)
        speechState = speech.state
    }

    /// Loop 10.3: 带 voiceId 的朗读(异步)— 用于火山引擎流式
    public func speak(_ text: String, voiceId: String) async {
        guard let speech else { return }
        guard ttsEnabled, !text.isEmpty else { return }
        await speech.speak(text, voiceId: voiceId)
        speechState = speech.state
    }

    /// 立刻停 TTS
    public func stopSpeaking() {
        guard let speech else { return }
        speech.stopSpeaking()
        speechState = speech.state
    }

    /// 切换 TTS 开关 — 关掉时如果正在播也停
    public func setTTSEnabled(_ enabled: Bool) {
        ttsEnabled = enabled
        if !enabled { stopSpeaking() }
    }

    // MARK: - Loop 10.3 UI: TTS 状态探测

    /// 调一次后端 /voice/tts/info 更新 ttsProviderStatus — 失败保留旧值
    /// UI 在 ChatView onAppear 调一次即可,不需要轮询
    public func probeTTSStatus() async {
        do {
            let info = try await api.ttsInfo()
            ttsProviderStatus = Self.classify(info: info)
        } catch {
            ttsProviderStatus = .unreachable(message: error.localizedDescription)
        }
    }

    /// 后端 info → UI 状态分类
    /// - provider == "mock" → .mock
    /// - provider == "volcengine" + configured=true → .volcengineReady
    /// - provider == "volcengine" + configured=false → .volcengineNotConfigured
    /// - 其它 provider 名 → .mock(防御,不该出现)
    static func classify(info: TTSInfo) -> TTSProviderStatus {
        if info.provider == "mock" { return .mock }
        if info.provider == "volcengine" {
            if info.configured {
                return .volcengineReady(
                    defaultVoice: info.defaultVoice,
                    endpoint: info.endpoint
                )
            }
            return .volcengineNotConfigured
        }
        return .mock
    }

    // MARK: - Loop 8: 自动抽取 + 摘要生成

    /// 异步触发 fact extraction
    /// - 用最近 6 条消息(3 轮)
    /// - 抽取结果走 MemoryStore.saveFact 落 SQLite
    /// - 失败 / 解析失败 → 静默(LLM 抽取是 best-effort,不影响聊天)
    private func triggerAutoMemoryExtraction() {
        guard let extractor = factExtractor else { return }
        // 避免并发抽取(单 LLM 实例)
        if extractionTask != nil { return }

        let recent = Array(messages.suffix(6)).map { ($0.role, $0.text) }
        guard recent.contains(where: { $0.0 == .user }) else { return }

        isAutoExtracting = true
        extractionTask = Task { [weak self] in
            guard let self else { return }
            // Task 从 @MainActor 方法启动,继承 MainActor 隔离
            defer {
                self.isAutoExtracting = false
                self.extractionTask = nil
            }

            let facts: [ExtractedFact]
            do {
                facts = try await extractor.extract(from: recent)
            } catch {
                // LLM 没准备好 / 生成失败 — 静默
                return
            }

            self.persistFacts(facts)
        }
    }

    private func persistFacts(_ facts: [ExtractedFact]) {
        let now = Date()
        for ef in facts {
            // 同 content + userId 已存在则跳过(去重)
            let existing = memory.listFacts(userId: userID, category: nil)
            if existing.contains(where: { $0.content == ef.content }) { continue }
            let fact = FactRecord(
                id: UUID().uuidString,
                userId: userID,
                category: ef.category,
                content: ef.content,
                confidence: ef.confidence,
                createdAt: now,
                lastAccessedAt: now,
                accessCount: 0,
                sourceMessageId: messages.last?.id.uuidString
            )
            memory.saveFact(fact)
        }
    }

    /// 每 summaryTriggerMessageCount 条 user 消息触发一次
    private func maybeTriggerSummary() {
        guard let generator = summaryGenerator else { return }
        let userCount = messages.filter { $0.role == .user }.count
        guard userCount > 0, userCount % summaryTriggerMessageCount == 0 else { return }

        let recent = Array(messages.suffix(20)).map { ($0.role, $0.text) }
        Task { [weak self] in
            guard let self else { return }
            let summary: String
            do {
                summary = try await generator.generate(from: recent)
            } catch {
                return
            }
            self.memory.saveShortTermSummary(
                conversationId: self.conversationID,
                summary: summary,
                messageCount: recent.count
            )
        }
    }
}

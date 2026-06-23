// 聊天 ViewModel — 调 /chat/stream,累积 text delta,处理 done/error
// Loop 7: 接入 MemoryStore — 每次 user send / 收到 assistant reply 都 appendAndPersist 到 SQLite;
//         init 时 hydrateWorking 把历史重新灌进内存,实现"刷新不丢"
// Loop 8: 接入端上 LLM(可选)— 收到 assistant 回复后异步调 FactExtractor;
//         每 10 轮调一次 SummaryGenerator;UI 用 isAutoExtracting 显示"自动记住"提示
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
    public let characterID: String
    public let characterName: String
    public let userID: String
    public let conversationID: String

    private let api: APIClientProtocol
    private let memory: MemoryStore
    private let factExtractor: FactExtractorProtocol?
    private let summaryGenerator: SummaryGeneratorProtocol?
    private var streamTask: Task<Void, Never>?
    private var extractionTask: Task<Void, Never>?

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
        summaryGenerator: SummaryGeneratorProtocol? = nil
    ) {
        self.characterID = characterID
        self.characterName = characterName
        self.userID = userID
        self.conversationID = conversationID
        self.api = api
        self.memory = memory
        self.factExtractor = factExtractor
        self.summaryGenerator = summaryGenerator
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
        if !currentStreamingText.isEmpty {
            let assistantMessage = ChatMessage(role: .assistant, text: currentStreamingText)
            messages.append(assistantMessage)
            try? memory.appendAndPersist(conversationId: conversationID, message: assistantMessage)
            currentStreamingText = ""

            // Loop 8: 收到 assistant 回复后异步触发抽取(LLM 未加载则跳过)
            triggerAutoMemoryExtraction()
            maybeTriggerSummary()
        }
        status = .done
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

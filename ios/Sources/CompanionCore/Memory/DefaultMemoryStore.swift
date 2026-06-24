/// DefaultMemoryStore — 4 层记忆的生产实现
///
/// 依赖:
/// - MessageRepository:Working 层持久化(冷启动重水合)、Short-term 摘要落 SQLite
/// - SummaryRepository:7 天滚动摘要
/// - FactRepository:长期事实(含 Loop 10.1 的 vec0 KNN 检索)
///
/// Working 层本身只是 in-memory cache,持久化由 MessageRepository 完成;
/// 这样 App kill 重启后,ChatViewModel.init() 调一次 loadHistory 重新灌进 Working。
///
/// Loop 10.1:`semanticSearch` 走 vec0 KNN;
/// - 有 embedding 服务 → query 文本 → embed → facts_vec MATCH → top-K → join facts
/// - 没服务 / vec0 不可用 → 降级回 substring scoring(原 Loop 7 实现)
import Foundation

public final class DefaultMemoryStore: MemoryStore, @unchecked Sendable {
    private let messages: MessageRepository
    private let summaries: SummaryRepository
    private let facts: FactRepository
    private let embeddingService: EmbeddingServiceProtocol?

    /// Working memory 的 in-memory 缓存;key = conversationId
    /// NSLock 是必要的 — @Observable ViewModel 在 MainActor 访问,Storage 调用可能在 background
    private let workingLock = NSLock()
    private var working: [String: [ChatMessage]] = [:]

    public init(
        messageRepository: MessageRepository,
        summaryRepository: SummaryRepository,
        factRepository: FactRepository,
        embeddingService: EmbeddingServiceProtocol? = nil
    ) {
        self.messages = messageRepository
        self.summaries = summaryRepository
        self.facts = factRepository
        self.embeddingService = embeddingService
    }

    // MARK: - Working

    public func workingMessages(conversationId: String) -> [ChatMessage] {
        workingLock.lock()
        defer { workingLock.unlock() }
        return working[conversationId] ?? []
    }

    public func hydrateWorking(conversationId: String) throws {
        let records = try messages.loadHistory(conversationId: conversationId, limit: 500)
        let chat = records.compactMap(Self.chatMessageFromRecord)
        workingLock.lock()
        working[conversationId] = chat
        workingLock.unlock()
    }

    public func appendAndPersist(conversationId: String, message: ChatMessage) throws {
        // 1. 落 SQLite
        let record = Self.recordFromChatMessage(conversationId: conversationId, message: message)
        try messages.save(record)
        // 2. 更新 in-memory 缓存
        workingLock.lock()
        working[conversationId, default: []].append(message)
        workingLock.unlock()
    }

    public func clearWorking(conversationId: String) {
        workingLock.lock()
        working.removeValue(forKey: conversationId)
        workingLock.unlock()
    }

    // MARK: - Short-term

    public func shortTermSummary(conversationId: String) -> String? {
        (try? summaries.latest(conversationId: conversationId))?.summary
    }

    public func saveShortTermSummary(conversationId: String, summary: String, messageCount: Int) {
        try? summaries.append(conversationId: conversationId, summary: summary, messageCount: messageCount)
    }

    // MARK: - Long-term

    public func saveFact(_ fact: FactRecord) {
        try? facts.save(fact)
    }

    public func listFacts(userId: String, category: FactRecord.Category?) -> [FactRecord] {
        (try? facts.list(userId: userId, category: category)) ?? []
    }

    public func forgetFact(id: String) {
        try? facts.forget(id)
    }

    // MARK: - Semantic (Loop 10.1: vec0 KNN;Loop 7 fallback: substring scoring)

    public func semanticSearch(userId: String, query: String, limit: Int) -> [FactRecord] {
        // 1) 优先走 vec0 KNN(若 embedding 服务 + DB 有 vec0 表)
        if let svc = embeddingService {
            let queryVec: [Float]? = Self.awaitBlocking { try await svc.embed(query) }
            if let queryVec, !queryVec.isEmpty {
                if let hits = try? facts.vectorSearch(userId: userId, queryEmbedding: queryVec, limit: limit),
                   !hits.isEmpty {
                    // touchAccess 标记召回,后续 MemoryStore 行为不变
                    for hit in hits {
                        try? facts.touchAccess(hit.fact.id)
                    }
                    return hits.map { $0.fact }
                }
            }
        }

        // 2) 降级:Loop 7 substring scoring(空格 + CJK 逐字)
        let all = listFacts(userId: userId, category: nil)
        let tokens = Self.tokens(in: query)
        if tokens.isEmpty {
            return Array(all.prefix(limit))
        }
        let scored = all.compactMap { fact -> (FactRecord, Int)? in
            let score = Self.matchScore(fact: fact, tokens: tokens)
            return score > 0 ? (fact, score) : nil
        }
        return scored
            .sorted { $0.1 > $1.1 }
            .prefix(limit)
            .map { $0.0 }
    }

    /// 同步等待一次异步调用 — MemoryStore.semanticSearch 是 sync 协议,但 embedding 服务是 async
    /// 用 DispatchSemaphore 单 call 等;调用方在 background 线程(NLLanguage.vector 在 detoured Task 里)
    private static func awaitBlocking(_ op: @Sendable @escaping () async throws -> [Float]?) -> [Float]? {
        let sem = DispatchSemaphore(value: 0)
        var result: [Float]? = nil
        Task.detached(priority: .userInitiated) {
            result = (try? await op()) ?? nil
            sem.signal()
        }
        sem.wait()
        return result
    }

    // MARK: - Helpers

    private static func chatMessageFromRecord(_ r: MessageRecord) -> ChatMessage? {
        // ChatMessage 只暴露 user / assistant;system 角色不上 UI(目前 backend 不产 system,
        // 保留此层做防御 — 以后接 agent tool message 时再扩 Role)
        guard let uuid = UUID(uuidString: r.id) else { return nil }
        let role: ChatMessage.Role
        switch r.role {
        case .user: role = .user
        case .assistant: role = .assistant
        case .system: return nil
        }
        return ChatMessage(
            id: uuid,
            role: role,
            text: r.content,
            createdAt: r.createdAt
        )
    }

    private static func recordFromChatMessage(conversationId: String, message: ChatMessage) -> MessageRecord {
        let role: MessageRecord.Role = message.role == .user ? .user : .assistant
        return MessageRecord(
            id: message.id.uuidString,
            conversationId: conversationId,
            role: role,
            content: message.text,
            toolCallsJSON: nil,
            tokenUsageJSON: nil,
            createdAt: message.createdAt
        )
    }

    /// 极简 token 切分:空格分英文,中文 / 日文 / 韩文逐字切
    private static func tokens(in text: String) -> [String] {
        var result: [String] = []
        var buffer = ""
        for scalar in text.unicodeScalars {
            // 简单规则:ASCII 字母 / 数字连续成 token,其它标点 / 空白分隔;CJK 逐字切
            if CharacterSet.alphanumerics.contains(scalar) && scalar.value < 128 {
                buffer.unicodeScalars.append(scalar)
            } else {
                if !buffer.isEmpty {
                    result.append(buffer.lowercased())
                    buffer = ""
                }
                if scalar.value >= 0x4E00 {
                    result.append(String(scalar))
                }
            }
        }
        if !buffer.isEmpty {
            result.append(buffer.lowercased())
        }
        return result
    }

    private static func matchScore(fact: FactRecord, tokens: [String]) -> Int {
        let haystack = fact.content.lowercased()
        var score = 0
        for token in tokens {
            if token.count == 1 {
                // CJK 单字匹配:每字 1 分
                if haystack.contains(token) { score += 1 }
            } else {
                // 英文 / 多字 token:整词命中 3 分,子串命中 1 分
                if haystack.contains(token) { score += 3 }
                else if haystack.contains(where: { token.contains(String($0)) }) { score += 1 }
            }
        }
        return score
    }
}
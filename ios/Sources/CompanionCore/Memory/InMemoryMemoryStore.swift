/// InMemoryMemoryStore — 测试用,不落 SQLite
///
/// 用于:
/// - ChatViewModel 单测(不想污染磁盘)
/// - 跑 swift build / swift test 时(避免 macOS 文件保护 API 报错)
import Foundation

public final class InMemoryMemoryStore: MemoryStore, @unchecked Sendable {
    private let lock = NSLock()
    private var working: [String: [ChatMessage]] = [:]
    private var summaries: [String: String] = [:]
    private var factList: [FactRecord] = []

    public init() {}

    public func workingMessages(conversationId: String) -> [ChatMessage] {
        lock.lock(); defer { lock.unlock() }
        return working[conversationId] ?? []
    }

    public func hydrateWorking(conversationId: String) throws {
        // No-op:InMemoryMemoryStore 测试场景不需要从 SQLite 重水合
    }

    public func appendAndPersist(conversationId: String, message: ChatMessage) throws {
        lock.lock()
        working[conversationId, default: []].append(message)
        lock.unlock()
    }

    public func clearWorking(conversationId: String) {
        lock.lock()
        working.removeValue(forKey: conversationId)
        lock.unlock()
    }

    public func shortTermSummary(conversationId: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        return summaries[conversationId]
    }

    public func saveShortTermSummary(conversationId: String, summary: String, messageCount: Int) {
        lock.lock()
        summaries[conversationId] = summary
        lock.unlock()
    }

    public func saveFact(_ fact: FactRecord) {
        lock.lock()
        factList.append(fact)
        lock.unlock()
    }

    public func listFacts(userId: String, category: FactRecord.Category?) -> [FactRecord] {
        lock.lock(); defer { lock.unlock() }
        return factList
            .filter { $0.userId == userId }
            .filter { category == nil || $0.category == category }
    }

    public func forgetFact(id: String) {
        lock.lock()
        factList.removeAll { $0.id == id }
        lock.unlock()
    }

    public func semanticSearch(userId: String, query: String, limit: Int) -> [FactRecord] {
        let facts = listFacts(userId: userId, category: nil)
        let tokens = query.lowercased().split(separator: " ").map(String.init)
        if tokens.isEmpty { return Array(facts.prefix(limit)) }
        return facts.filter { fact in
            let haystack = fact.content.lowercased()
            return tokens.contains(where: haystack.contains)
        }.prefix(limit).map { $0 }
    }
}

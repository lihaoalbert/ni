// MemoryStore 单元测试 — DefaultMemoryStore + InMemoryMemoryStore 协议一致性
import XCTest
@testable import CompanionCore

final class MemoryStoreTests: XCTestCase {
    var db: Database!
    var messageRepo: MessageRepository!
    var summaryRepo: SummaryRepository!
    var factRepo: FactRepository!
    var store: DefaultMemoryStore!

    override func setUp() {
        super.setUp()
        db = try! Database.inMemory()
        messageRepo = MessageRepository(database: db)
        summaryRepo = SummaryRepository(database: db)
        factRepo = FactRepository(database: db)
        store = DefaultMemoryStore(
            messageRepository: messageRepo,
            summaryRepository: summaryRepo,
            factRepository: factRepo
        )
    }

    // MARK: - Working Memory

    func testAppendAndPersist_WritesToSQLiteAndMemory() throws {
        let convID = "conv_w1"
        // FK: messages.conversation_id → conversations.id,先 upsert
        _ = try ConversationRepository(database: db).upsert(
            id: convID, characterId: "ip_001", characterName: "苏晚"
        )

        let msg = ChatMessage(role: .user, text: "你好")
        try store.appendAndPersist(conversationId: convID, message: msg)

        // in-memory
        let working = store.workingMessages(conversationId: convID)
        XCTAssertEqual(working.count, 1)
        XCTAssertEqual(working.first?.text, "你好")

        // SQLite
        let persisted = try messageRepo.loadHistory(conversationId: convID)
        XCTAssertEqual(persisted.count, 1)
        XCTAssertEqual(persisted.first?.content, "你好")
    }

    func testHydrateWorking_LoadsFromSQLite() throws {
        let convID = "conv_h1"
        _ = try ConversationRepository(database: db).upsert(
            id: convID, characterId: "ip_001", characterName: "苏晚"
        )

        // 直接写入 SQLite(模拟 App 上一会话已存)
        let msg1 = ChatMessage(role: .user, text: "你好", createdAt: Date(timeIntervalSince1970: 100))
        let msg2 = ChatMessage(role: .assistant, text: "你好呀", createdAt: Date(timeIntervalSince1970: 101))
        try store.appendAndPersist(conversationId: convID, message: msg1)
        try store.appendAndPersist(conversationId: convID, message: msg2)

        // 清掉 in-memory 缓存
        store.clearWorking(conversationId: convID)
        XCTAssertTrue(store.workingMessages(conversationId: convID).isEmpty)

        // 重新 hydrate
        try store.hydrateWorking(conversationId: convID)
        let restored = store.workingMessages(conversationId: convID)
        XCTAssertEqual(restored.count, 2)
        XCTAssertEqual(restored.map { $0.text }, ["你好", "你好呀"])
    }

    func testClearWorking_DoesNotDeleteSQLite() throws {
        let convID = "conv_c1"
        _ = try ConversationRepository(database: db).upsert(
            id: convID, characterId: "ip_001", characterName: "苏晚"
        )
        try store.appendAndPersist(conversationId: convID, message: ChatMessage(role: .user, text: "x"))

        store.clearWorking(conversationId: convID)

        XCTAssertTrue(store.workingMessages(conversationId: convID).isEmpty)
        let stillThere = try messageRepo.loadHistory(conversationId: convID)
        XCTAssertEqual(stillThere.count, 1, "SQLite should still hold the message")
    }

    // MARK: - Short-term

    func testShortTermSummary_RoundTrip() throws {
        let convID = "conv_s1"
        // summaries.conversation_id 外键 → conversations.id,需要先 upsert conversation
        _ = try ConversationRepository(database: db).upsert(
            id: convID, characterId: "ip_001", characterName: "苏晚"
        )
        XCTAssertNil(store.shortTermSummary(conversationId: convID))

        store.saveShortTermSummary(conversationId: convID, summary: "用户问 AI 数字人", messageCount: 5)
        let got = store.shortTermSummary(conversationId: convID)
        XCTAssertEqual(got, "用户问 AI 数字人")
    }

    // MARK: - Long-term

    func testSaveAndListFacts() {
        let userID = "user-l1"
        let now = Date()
        let f1 = FactRecord(
            id: UUID().uuidString, userId: userID, category: .preference,
            content: "喜欢爵士乐", confidence: 0.8, createdAt: now,
            lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        )
        store.saveFact(f1)
        let all = store.listFacts(userId: userID, category: nil)
        XCTAssertEqual(all.count, 1)
        XCTAssertEqual(all.first?.content, "喜欢爵士乐")
    }

    func testForgetFact() {
        let userID = "user-f1"
        let now = Date()
        let f = FactRecord(
            id: "to-forget", userId: userID, category: .basic,
            content: "x", confidence: 0.5, createdAt: now,
            lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        )
        store.saveFact(f)
        XCTAssertEqual(store.listFacts(userId: userID, category: nil).count, 1)

        store.forgetFact(id: "to-forget")
        XCTAssertTrue(store.listFacts(userId: userID, category: nil).isEmpty)
    }

    // MARK: - Semantic (Loop 7 skeleton)

    func testSemanticSearch_KeywordMatch() {
        let userID = "user-sem1"
        let now = Date()
        store.saveFact(FactRecord(
            id: "1", userId: userID, category: .preference,
            content: "用户喜欢爵士乐和老电影", confidence: 0.8,
            createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))
        store.saveFact(FactRecord(
            id: "2", userId: userID, category: .work,
            content: "软件工程师", confidence: 0.7,
            createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))

        let matched = store.semanticSearch(userId: userID, query: "爵士乐", limit: 5)
        XCTAssertEqual(matched.count, 1)
        XCTAssertEqual(matched.first?.id, "1")
    }

    func testSemanticSearch_NoMatchReturnsEmpty() {
        let userID = "user-sem2"
        store.saveFact(FactRecord(
            id: "1", userId: userID, category: .preference,
            content: "爵士乐", confidence: 0.8,
            createdAt: Date(), lastAccessedAt: Date(), accessCount: 0, sourceMessageId: nil
        ))

        let matched = store.semanticSearch(userId: userID, query: "足球", limit: 5)
        XCTAssertTrue(matched.isEmpty)
    }

    // MARK: - InMemory parity

    func testInMemoryStore_ImplementsProtocol() throws {
        let inMem = InMemoryMemoryStore()
        let convID = "conv_inmem"

        try inMem.appendAndPersist(conversationId: convID, message: ChatMessage(role: .user, text: "hi"))

        let got = inMem.workingMessages(conversationId: convID)
        XCTAssertEqual(got.count, 1)
        XCTAssertEqual(got.first?.text, "hi")
    }
}

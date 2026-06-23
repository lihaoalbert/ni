// Repository 单元测试 — Conversation / Message / Fact / Summary CRUD
import XCTest
@testable import CompanionCore

final class RepositoryTests: XCTestCase {
    var db: Database!
    var conversations: ConversationRepository!
    var messages: MessageRepository!
    var facts: FactRepository!
    var summaries: SummaryRepository!

    override func setUp() {
        super.setUp()
        db = try! Database.inMemory()
        conversations = ConversationRepository(database: db)
        messages = MessageRepository(database: db)
        facts = FactRepository(database: db)
        summaries = SummaryRepository(database: db)
    }

    // MARK: - ConversationRepository

    func testConversationUpsert_NewRecord() throws {
        let id = "conv_test_1"
        let result = try conversations.upsert(
            id: id, characterId: "ip_001", characterName: "苏晚"
        )
        XCTAssertEqual(result.id, id)
        XCTAssertEqual(result.characterId, "ip_001")
        XCTAssertEqual(result.characterName, "苏晚")

        let fetched = try conversations.findById(id)
        XCTAssertNotNil(fetched)
        XCTAssertEqual(fetched?.characterName, "苏晚")
    }

    func testConversationUpsert_Idempotent() throws {
        let id = "conv_test_2"
        let first = try conversations.upsert(id: id, characterId: "ip_001", characterName: "苏晚")
        let second = try conversations.upsert(id: id, characterId: "ip_001", characterName: "苏晚改名")
        XCTAssertEqual(first.characterName, "苏晚")
        XCTAssertEqual(second.characterName, "苏晚", "second upsert should return existing record")
    }

    func testConversationListByCharacter_OrderedByLastMessageDesc() throws {
        try conversations.upsert(id: "c1", characterId: "ip_001", characterName: "苏晚", now: Date(timeIntervalSince1970: 1000))
        try conversations.upsert(id: "c2", characterId: "ip_001", characterName: "苏晚", now: Date(timeIntervalSince1970: 3000))
        try conversations.upsert(id: "c3", characterId: "ip_002", characterName: "陆星河", now: Date(timeIntervalSince1970: 2000))

        let list = try conversations.listByCharacter("ip_001")
        XCTAssertEqual(list.count, 2)
        XCTAssertEqual(list.first?.id, "c2", "newest first")
    }

    func testConversationTouchLastMessage() throws {
        let id = "c1"
        let initial = try conversations.upsert(id: id, characterId: "ip_001", characterName: "苏晚")
        let originalTime = initial.lastMessageAt

        // sleep 10ms 再 touch,用 now(>originalTime)而不是 1970 时间戳
        Thread.sleep(forTimeInterval: 0.05)
        try conversations.touchLastMessage(id, at: Date())

        let updated = try conversations.findById(id)
        XCTAssertNotNil(updated)
        XCTAssertGreaterThan(updated!.lastMessageAt, originalTime)
    }

    // MARK: - MessageRepository

    func testMessageSaveAndLoadHistory() throws {
        let convID = "conv_msg"
        _ = try conversations.upsert(id: convID, characterId: "ip_001", characterName: "苏晚")

        let now = Date()
        try messages.save(MessageRecord(
            id: UUID().uuidString, conversationId: convID, role: .user,
            content: "你好", createdAt: now
        ))
        try messages.save(MessageRecord(
            id: UUID().uuidString, conversationId: convID, role: .assistant,
            content: "你好,我是苏晚", createdAt: now.addingTimeInterval(1)
        ))

        let history = try messages.loadHistory(conversationId: convID)
        XCTAssertEqual(history.count, 2)
        XCTAssertEqual(history[0].content, "你好")
        XCTAssertEqual(history[1].content, "你好,我是苏晚")
        XCTAssertEqual(history[0].role, .user)
        XCTAssertEqual(history[1].role, .assistant)
    }

    func testMessageLoadHistory_OrderedAsc() throws {
        let convID = "conv_order"
        _ = try conversations.upsert(id: convID, characterId: "ip_001", characterName: "苏晚")

        let now = Date()
        // 倒序插入,验证 loadHistory 按 ASC 返回
        for i in stride(from: 5, through: 1, by: -1) {
            try messages.save(MessageRecord(
                id: UUID().uuidString, conversationId: convID, role: .user,
                content: "msg-\(i)", createdAt: now.addingTimeInterval(TimeInterval(i))
            ))
        }

        let history = try messages.loadHistory(conversationId: convID)
        XCTAssertEqual(history.map { $0.content }, ["msg-1", "msg-2", "msg-3", "msg-4", "msg-5"])
    }

    func testMessageCascadeDelete() throws {
        let convID = "conv_cascade"
        _ = try conversations.upsert(id: convID, characterId: "ip_001", characterName: "苏晚")
        try messages.save(MessageRecord(
            id: UUID().uuidString, conversationId: convID, role: .user,
            content: "应该被一起删", createdAt: Date()
        ))

        try conversations.delete(convID)
        let count = try messages.count(conversationId: convID)
        XCTAssertEqual(count, 0, "deleting conversation should cascade delete messages")
    }

    // MARK: - FactRepository

    func testFactSaveAndList() throws {
        let userID = "user-001"
        let now = Date()
        try facts.save(FactRecord(
            id: UUID().uuidString, userId: userID, category: .preference,
            content: "喜欢爵士乐", confidence: 0.9, createdAt: now,
            lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))
        try facts.save(FactRecord(
            id: UUID().uuidString, userId: userID, category: .basic,
            content: "软件工程师", confidence: 0.7, createdAt: now,
            lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))
        try facts.save(FactRecord(
            id: UUID().uuidString, userId: "other-user", category: .basic,
            content: "无关 user", confidence: 0.5, createdAt: now,
            lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))

        let mine = try facts.list(userId: userID)
        XCTAssertEqual(mine.count, 2, "should filter by userId")
        // preference (0.9) 应该排第一
        XCTAssertEqual(mine.first?.category, .preference)
    }

    func testFactListFilteredByCategory() throws {
        let userID = "user-filter"
        let now = Date()
        for cat in [FactRecord.Category.basic, .preference, .preference] {
            try facts.save(FactRecord(
                id: UUID().uuidString, userId: userID, category: cat,
                content: "fact-\(cat.rawValue)", confidence: 0.5, createdAt: now,
                lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
            ))
        }

        let prefs = try facts.list(userId: userID, category: .preference)
        XCTAssertEqual(prefs.count, 2)
        XCTAssertTrue(prefs.allSatisfy { $0.category == .preference })
    }

    func testFactTouchAccess_IncrementsCount() throws {
        let id = UUID().uuidString
        let now = Date()
        try facts.save(FactRecord(
            id: id, userId: "u", category: .basic, content: "x",
            confidence: 0.5, createdAt: now, lastAccessedAt: now,
            accessCount: 0, sourceMessageId: nil
        ))

        try facts.touchAccess(id, at: now.addingTimeInterval(1))
        try facts.touchAccess(id, at: now.addingTimeInterval(2))

        let fetched = try facts.list(userId: "u").first
        XCTAssertEqual(fetched?.accessCount, 2)
    }

    func testFactForget() throws {
        let id = UUID().uuidString
        let now = Date()
        try facts.save(FactRecord(
            id: id, userId: "u", category: .basic, content: "要删除",
            confidence: 0.5, createdAt: now, lastAccessedAt: now,
            accessCount: 0, sourceMessageId: nil
        ))
        try facts.forget(id)
        let fetched = try facts.list(userId: "u")
        XCTAssertTrue(fetched.isEmpty)
    }

    // MARK: - SummaryRepository

    func testSummaryAppendAndLatest() throws {
        let convID = "conv_sum"
        _ = try conversations.upsert(id: convID, characterId: "ip_001", characterName: "苏晚")

        try summaries.append(conversationId: convID, summary: "用户问了 AI 数字人话题", messageCount: 5)
        try summaries.append(conversationId: convID, summary: "用户提到想做短剧", messageCount: 12)
        try summaries.append(conversationId: convID, summary: "用户计划明年代理 MuseTalk", messageCount: 20)

        let latest = try summaries.latest(conversationId: convID)
        XCTAssertEqual(latest?.summary, "用户计划明年代理 MuseTalk")
        XCTAssertEqual(latest?.messageCount, 20)
    }

    func testSummaryList_OrderedDesc() throws {
        let convID = "conv_sum_list"
        _ = try conversations.upsert(id: convID, characterId: "ip_001", characterName: "苏晚")

        for i in 1...3 {
            try summaries.append(conversationId: convID, summary: "s-\(i)", messageCount: i)
        }

        let list = try summaries.list(conversationId: convID)
        XCTAssertEqual(list.count, 3)
        XCTAssertEqual(list.first?.summary, "s-3")
    }
}

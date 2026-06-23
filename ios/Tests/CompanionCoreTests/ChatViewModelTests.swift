// ChatViewModel 集成测试 — send → 落盘 → 新 ViewModel 重新加载 → 历史可见
import XCTest
@testable import CompanionCore

final class ChatViewModelTests: XCTestCase {
    var db: Database!
    var memory: DefaultMemoryStore!
    var mockAPI: MockAPIClient!

    @MainActor
    override func setUp() {
        super.setUp()
        db = try! Database.inMemory()
        memory = DefaultMemoryStore(
            messageRepository: MessageRepository(database: db),
            summaryRepository: SummaryRepository(database: db),
            factRepository: FactRepository(database: db)
        )
        mockAPI = MockAPIClient()
    }

    @MainActor
    func testSend_AppendsToMessagesAndPersists() async throws {
        let vm = makeViewModel(characterID: "ip_001", characterName: "苏晚")

        // 设置 mock 返回一个文本事件 + done
        mockAPI.eventsToYield = [
            .text("你好"),
            .text(",苏晚"),
            .done,
        ]
        mockAPI.assistantText = "你好,苏晚"

        vm.send("你好")
        try await Task.sleep(nanoseconds: 200_000_000)  // 200ms 等流完成

        // Working memory 应该包含 2 条:user + assistant
        XCTAssertEqual(vm.messages.count, 2)
        XCTAssertEqual(vm.messages[0].role, .user)
        XCTAssertEqual(vm.messages[0].text, "你好")
        XCTAssertEqual(vm.messages[1].role, .assistant)
        XCTAssertEqual(vm.messages[1].text, "你好,苏晚")

        // SQLite 也应持久化
        let convID = vm.conversationID
        let persisted = try MessageRepository(database: db).loadHistory(conversationId: convID)
        XCTAssertEqual(persisted.count, 2)
    }

    @MainActor
    func testNewViewModel_LoadsHistoryFromDisk() async throws {
        let characterID = "ip_001"
        let characterName = "苏晚"
        let convID = "conv_\(AppConfig.localUserID)_\(characterID)"

        // 1) 第一个 ViewModel 发一条消息
        let vm1 = makeViewModel(characterID: characterID, characterName: characterName)
        mockAPI.eventsToYield = [.text("first reply"), .done]
        vm1.send("first user message")
        try await Task.sleep(nanoseconds: 200_000_000)

        XCTAssertEqual(vm1.messages.count, 2)

        // 2) 第二个 ViewModel(模拟 App 重启,新的实例)应看到这段历史
        let vm2 = makeViewModel(characterID: characterID, characterName: characterName)
        XCTAssertEqual(vm2.messages.count, 2, "new VM should hydrate history")
        XCTAssertEqual(vm2.messages[0].text, "first user message")
        XCTAssertEqual(vm2.messages[1].text, "first reply")
        XCTAssertEqual(vm2.conversationID, convID, "conversationID should be stable across VMs")
    }

    @MainActor
    func testSaveFact_PersistsToSQLite() async {
        let vm = makeViewModel(characterID: "ip_001", characterName: "苏晚")

        // 直接调 saveUserMessageAsFact(无需 mock 流)
        let msg = ChatMessage(role: .user, text: "我叫李明,在做 AI 陪伴 App")
        vm.saveUserMessageAsFact(msg, category: .basic)

        // 验证 SQLite
        let facts = try! FactRepository(database: db).list(userId: vm.userID)
        XCTAssertEqual(facts.count, 1)
        XCTAssertEqual(facts.first?.content, "我叫李明,在做 AI 陪伴 App")
        XCTAssertEqual(facts.first?.category, .basic)
    }

    @MainActor
    func testSend_EmptyTextIgnored() {
        let vm = makeViewModel(characterID: "ip_001", characterName: "苏晚")
        let originalCount = vm.messages.count
        vm.send("   \n  ")
        XCTAssertEqual(vm.messages.count, originalCount, "empty text should not be sent")
    }

    // MARK: - Helpers

    @MainActor
    private func makeViewModel(characterID: String, characterName: String) -> ChatViewModel {
        // 保证 conversation 行存在
        _ = try? ConversationRepository(database: db).upsert(
            id: "conv_\(AppConfig.localUserID)_\(characterID)",
            characterId: characterID,
            characterName: characterName
        )
        return ChatViewModel(
            characterID: characterID,
            characterName: characterName,
            userID: AppConfig.localUserID,
            conversationID: "conv_\(AppConfig.localUserID)_\(characterID)",
            api: mockAPI,
            memory: memory
        )
    }
}

// MARK: - Mock API

final class MockAPIClient: APIClientProtocol, @unchecked Sendable {
    var eventsToYield: [SSEEvent] = []
    var assistantText: String = ""

    func login(email: String, password: String) async throws -> AuthToken {
        AuthToken(
            accessToken: "mock-token",
            refreshToken: "mock-refresh",
            tokenType: "Bearer",
            expiresIn: 3600
        )
    }

    func listIPs() async throws -> IPListResponse {
        IPListResponse(items: [], total: 0, page: 1, pageSize: 20, hasMore: false)
    }

    func getIP(id: String) async throws -> CharacterDetail {
        throw APIError.notFound
    }

    func streamChat(
        userID: String,
        characterID: String,
        message: String
    ) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            // 模拟网络延迟 + 事件流
            Task { @Sendable in
                try? await Task.sleep(nanoseconds: 50_000_000)
                for event in self.eventsToYield {
                    continuation.yield(event)
                    try? await Task.sleep(nanoseconds: 5_000_000)
                }
                continuation.finish()
            }
        }
    }
}

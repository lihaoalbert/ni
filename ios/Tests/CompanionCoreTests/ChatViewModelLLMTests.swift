// ChatViewModel LLM 集成测试 — 收到 assistant 回复后异步触发 fact 抽取 + 摘要
import XCTest
@testable import CompanionCore

@MainActor
final class ChatViewModelLLMTests: XCTestCase {
    var db: Database!
    var memory: DefaultMemoryStore!
    var mockAPI: MockAPIClient!
    var mockLLM: MockOnDeviceLLM!
    var extractor: FactExtractor!
    var summarizer: SummaryGenerator!

    override func setUp() {
        super.setUp()
        db = try! Database.inMemory()
        memory = DefaultMemoryStore(
            messageRepository: MessageRepository(database: db),
            summaryRepository: SummaryRepository(database: db),
            factRepository: FactRepository(database: db)
        )
        mockAPI = MockAPIClient()
        mockLLM = MockOnDeviceLLM()
        extractor = FactExtractor(llm: mockLLM)
        summarizer = SummaryGenerator(llm: mockLLM)
    }

    func testAutoExtraction_TriggersAfterAssistantReply() async throws {
        mockLLM.nextResponse = """
        [{"category":"basic","content":"叫李明","confidence":0.9},{"category":"work","content":"做后端","confidence":0.85}]
        """
        mockAPI.eventsToYield = [.text("你好李明"), .done]

        let vm = makeViewModel()
        vm.send("我叫李明,在做后端")
        try await Task.sleep(nanoseconds: 500_000_000)

        // 等异步抽取完成(500ms 足够)
        try await Task.sleep(nanoseconds: 500_000_000)

        let facts = memory.listFacts(userId: vm.userID, category: nil)
        XCTAssertGreaterThanOrEqual(facts.count, 1, "should auto-extract at least one fact")
        XCTAssertTrue(facts.contains(where: { $0.content == "叫李明" }))
        XCTAssertTrue(facts.contains(where: { $0.content == "做后端" }))
    }

    func testAutoExtraction_SkippedWhenLLMReturnsEmpty() async throws {
        mockLLM.nextResponse = "[]"
        mockAPI.eventsToYield = [.text("嗯嗯"), .done]

        let vm = makeViewModel()
        vm.send("今天天气真好")
        try await Task.sleep(nanoseconds: 500_000_000)
        try await Task.sleep(nanoseconds: 500_000_000)

        XCTAssertTrue(memory.listFacts(userId: vm.userID, category: nil).isEmpty)
    }

    func testAutoExtraction_LLMError_DoesNotCrash() async throws {
        mockLLM.nextError = OnDeviceLLMError.modelNotLoaded
        mockAPI.eventsToYield = [.text("hi"), .done]

        let vm = makeViewModel()
        vm.send("hi")
        try await Task.sleep(nanoseconds: 500_000_000)
        try await Task.sleep(nanoseconds: 500_000_000)

        // 失败被吞掉,VM 正常
        XCTAssertEqual(vm.messages.count, 2)
        XCTAssertFalse(vm.isAutoExtracting, "extraction should reset after error")
    }

    func testAutoExtraction_DedupesExistingFacts() async throws {
        // 第一次抽取
        mockLLM.nextResponse = """
        [{"category":"basic","content":"叫李明","confidence":0.9}]
        """
        mockAPI.eventsToYield = [.text("你好"), .done]
        let vm = makeViewModel()
        vm.send("我叫李明")
        try await Task.sleep(nanoseconds: 1_000_000_000)

        let firstCount = memory.listFacts(userId: vm.userID, category: nil).count
        XCTAssertEqual(firstCount, 1)

        // 第二次,LLM 再次返回同一条 — 应去重
        mockAPI.eventsToYield = [.text("又见面了"), .done]
        vm.send("我叫李明,记得吗?")
        try await Task.sleep(nanoseconds: 1_000_000_000)

        let secondCount = memory.listFacts(userId: vm.userID, category: nil).count
        XCTAssertEqual(secondCount, 1, "should not duplicate existing fact")
    }

    func testSummary_TriggeredEvery10UserMessages() async throws {
        mockLLM.nextResponse = "[]"  // 避免每次触发 fact 抽取干扰
        mockAPI.eventsToYield = [.text("ok"), .done]
        let summaryMock = MockOnDeviceLLM()
        summaryMock.nextResponse = "用户在做某事"
        let summarizerWithMock = SummaryGenerator(llm: summaryMock)

        let vm = ChatViewModel(
            characterID: "ip_001",
            characterName: "苏晚",
            userID: AppConfig.localUserID,
            conversationID: "conv_test_summary",
            api: mockAPI,
            memory: memory,
            factExtractor: nil,  // 不抽 fact,避免干扰
            summaryGenerator: summarizerWithMock
        )
        _ = try? ConversationRepository(database: db).upsert(
            id: "conv_test_summary", characterId: "ip_001", characterName: "苏晚"
        )

        // 发 10 条 user 消息
        for i in 1...10 {
            mockAPI.eventsToYield = [.text("reply \(i)"), .done]
            vm.send("user \(i)")
            try await Task.sleep(nanoseconds: 100_000_000)
        }

        // 等 summary 生成
        try await Task.sleep(nanoseconds: 1_500_000_000)

        let latest = memory.shortTermSummary(conversationId: "conv_test_summary")
        XCTAssertNotNil(latest, "summary should be generated after 10 user messages")
        XCTAssertEqual(summaryMock.lastPrompt?.contains("user 10"), true)
    }

    // MARK: - Helpers

    @MainActor
    private func makeViewModel() -> ChatViewModel {
        let convID = "conv_\(AppConfig.localUserID)_ip_001"
        _ = try? ConversationRepository(database: db).upsert(
            id: convID, characterId: "ip_001", characterName: "苏晚"
        )
        return ChatViewModel(
            characterID: "ip_001",
            characterName: "苏晚",
            userID: AppConfig.localUserID,
            conversationID: convID,
            api: mockAPI,
            memory: memory,
            factExtractor: extractor,
            summaryGenerator: summarizer
        )
    }
}

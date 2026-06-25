// Loop 10.3 UI: TTS 状态探测 + 分类
import XCTest
@testable import CompanionCore

@MainActor
final class TTSProviderStatusTests: XCTestCase {
    private var mockAPI: MockAPIClient!
    private var db: Database!
    private var memory: DefaultMemoryStore!

    override func setUp() async throws {
        try await super.setUp()
        mockAPI = MockAPIClient()
        db = try Database.inMemory()
        memory = DefaultMemoryStore(
            messageRepository: MessageRepository(database: db),
            summaryRepository: SummaryRepository(database: db),
            factRepository: FactRepository(database: db)
        )
        _ = try? ConversationRepository(database: db).upsert(
            id: "conv_test", characterId: "char1", characterName: "Test"
        )
    }

    private func makeVM() -> ChatViewModel {
        ChatViewModel(
            characterID: "char1",
            characterName: "Test",
            userID: AppConfig.localUserID,
            conversationID: "conv_test",
            api: mockAPI,
            memory: memory
        )
    }

    // MARK: - classify 静态逻辑(纯函数,无 IO)

    func testClassify_mockProvider() {
        let info = TTSInfo(
            provider: "mock", configured: false, defaultVoice: "",
            endpoint: "", cacheBackend: "memory", cacheMaxSize: 0
        )
        XCTAssertEqual(ChatViewModel.classify(info: info), .mock)
    }

    func testClassify_volcengineConfigured() {
        let info = TTSInfo(
            provider: "volcengine", configured: true,
            defaultVoice: "zh_female_qingxin", endpoint: "openspeech.bytedance.com",
            cacheBackend: "memory", cacheMaxSize: 128
        )
        XCTAssertEqual(
            ChatViewModel.classify(info: info),
            .volcengineReady(defaultVoice: "zh_female_qingxin", endpoint: "openspeech.bytedance.com")
        )
    }

    func testClassify_volcengineNotConfigured() {
        let info = TTSInfo(
            provider: "volcengine", configured: false,
            defaultVoice: "zh_female_qingxin", endpoint: "openspeech.bytedance.com",
            cacheBackend: "memory", cacheMaxSize: 128
        )
        XCTAssertEqual(ChatViewModel.classify(info: info), .volcengineNotConfigured)
    }

    func testClassify_unknownProvider_fallsBackToMock() {
        // 防御:后端有第三种 provider 时不崩
        let info = TTSInfo(
            provider: "elevenlabs", configured: true, defaultVoice: "voice_a",
            endpoint: "api.elevenlabs.io", cacheBackend: "redis", cacheMaxSize: 1000
        )
        XCTAssertEqual(ChatViewModel.classify(info: info), .mock)
    }

    // MARK: - probeTTSStatus 走 API + 改状态

    func testProbe_mockProvider_setsStatus() async {
        mockAPI.nextTTSInfo = TTSInfo(
            provider: "mock", configured: false, defaultVoice: "",
            endpoint: "", cacheBackend: "memory", cacheMaxSize: 0
        )
        let vm = makeVM()
        XCTAssertEqual(vm.ttsProviderStatus, .unknown)
        await vm.probeTTSStatus()
        XCTAssertEqual(vm.ttsProviderStatus, .mock)
        XCTAssertEqual(mockAPI.ttsInfoCallCount, 1)
    }

    func testProbe_volcengineReady_setsStatus() async {
        mockAPI.nextTTSInfo = TTSInfo(
            provider: "volcengine", configured: true,
            defaultVoice: "zh_female_qingxin", endpoint: "openspeech.bytedance.com",
            cacheBackend: "memory", cacheMaxSize: 128
        )
        let vm = makeVM()
        await vm.probeTTSStatus()
        XCTAssertEqual(
            vm.ttsProviderStatus,
            .volcengineReady(defaultVoice: "zh_female_qingxin", endpoint: "openspeech.bytedance.com")
        )
    }

    func testProbe_apiError_setsUnreachable() async {
        mockAPI.nextTTSInfoError = APIError.transport(URLError(.notConnectedToInternet))
        let vm = makeVM()
        await vm.probeTTSStatus()
        if case .unreachable = vm.ttsProviderStatus {
            // pass
        } else {
            XCTFail("期望 .unreachable,实际 \(vm.ttsProviderStatus)")
        }
    }

    func testProbe_calledMultipleTimes_incrementsCallCount() async {
        mockAPI.nextTTSInfo = TTSInfo(
            provider: "mock", configured: false, defaultVoice: "",
            endpoint: "", cacheBackend: "memory", cacheMaxSize: 0
        )
        let vm = makeVM()
        await vm.probeTTSStatus()
        await vm.probeTTSStatus()
        await vm.probeTTSStatus()
        XCTAssertEqual(mockAPI.ttsInfoCallCount, 3)
    }
}

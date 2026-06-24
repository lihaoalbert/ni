// ChatViewModelVoiceTests — 语音方法集成测试
// startListening / stopListeningAndSend / speak / stopSpeaking 流程 + 自动 TTS 触发
import XCTest
@testable import CompanionCore

@MainActor
final class ChatViewModelVoiceTests: XCTestCase {
    var db: Database!
    var memory: DefaultMemoryStore!
    var mockAPI: MockAPIClient!
    var mockSpeech: MockSpeechService!

    override func setUp() {
        super.setUp()
        db = try! Database.inMemory()
        memory = DefaultMemoryStore(
            messageRepository: MessageRepository(database: db),
            summaryRepository: SummaryRepository(database: db),
            factRepository: FactRepository(database: db)
        )
        mockAPI = MockAPIClient()
        mockSpeech = MockSpeechService()
    }

    // MARK: - 启动监听

    func testStartListening_PermissionDenied_StaysIdle() async {
        mockSpeech.permissionStatus = .denied
        let vm = makeViewModel()
        await vm.startListening()
        XCTAssertFalse(vm.isListening)
    }

    func testStartListening_Granted_EntersListening() async throws {
        mockSpeech.permissionStatus = .undetermined
        mockSpeech.nextPermissionResult = .granted
        let vm = makeViewModel()

        await vm.startListening()

        XCTAssertTrue(vm.isListening, "isListening should be true after startListening")
        XCTAssertEqual(mockSpeech.permissionRequestCount, 1)
    }

    func testStartListening_TwiceSecondIsNoop() async throws {
        mockSpeech.permissionStatus = .granted
        let vm = makeViewModel()
        await vm.startListening()
        let streamTaskCount_before = mockSpeech.scheduledPartials.count  // sanity
        _ = streamTaskCount_before

        await vm.startListening()  // 第二次
        // 第二次不应该重启 — 状态仍是 listening
        XCTAssertTrue(vm.isListening)
    }

    // MARK: - 停 + 发送

    func testStopListeningAndSend_EmptyTranscript_NoSend() async throws {
        mockSpeech.permissionStatus = .granted
        let vm = makeViewModel()
        await vm.startListening()
        // 不 yield 任何 partial

        await vm.stopListeningAndSend()

        // 状态应回到 idle,消息只有可能的初始空消息(这里没发任何,消息数 = 0)
        XCTAssertFalse(vm.isListening)
        XCTAssertEqual(vm.messages.count, 0, "no transcript → no message")
    }

    func testStopListeningAndSend_TranscriptTriggersSend() async throws {
        mockSpeech.permissionStatus = .granted
        mockSpeech.scheduledPartials = ["你好世界"]
        mockAPI.eventsToYield = [.text("hi"), .done]
        let vm = makeViewModel()
        await vm.startListening()

        // 模拟 partial 流
        mockSpeech.simulatePartials()

        // 等 UI 收到 partial(0.2s)
        try await Task.sleep(nanoseconds: 200_000_000)

        // 松手 — 触发 send
        await vm.stopListeningAndSend()

        // 等流式回复完成
        try await Task.sleep(nanoseconds: 300_000_000)

        XCTAssertGreaterThanOrEqual(vm.messages.count, 2, "user + assistant 应该有")
        XCTAssertEqual(vm.messages[0].text, "你好世界", "user 消息来自 transcript")
    }

    // MARK: - 取消

    func testCancelListening_DiscardsTranscript() async throws {
        mockSpeech.permissionStatus = .granted
        mockSpeech.scheduledPartials = ["不要发"]
        let vm = makeViewModel()
        await vm.startListening()
        mockSpeech.simulatePartials()
        try await Task.sleep(nanoseconds: 200_000_000)

        vm.cancelListening()

        XCTAssertFalse(vm.isListening)
        XCTAssertEqual(vm.currentListeningTranscript, "")
        XCTAssertEqual(vm.messages.count, 0, "cancel 不应 send")
    }

    // MARK: - TTS 自动触发

    func testCommitStreamedMessage_TriggersAutoTTS() async throws {
        mockSpeech.permissionStatus = .granted
        mockAPI.eventsToYield = [.text("你好"), .text("呀"), .done]
        let vm = makeViewModel()
        vm.setTTSEnabled(true)
        XCTAssertTrue(vm.ttsEnabled)

        vm.send("hi")
        try await Task.sleep(nanoseconds: 300_000_000)  // 等流完成 + commit

        XCTAssertEqual(mockSpeech.speakCallCount, 1, "完成一条 assistant 后应自动 TTS")
        XCTAssertEqual(mockSpeech.lastSpokenText, "你好呀")
    }

    func testCommitStreamedMessage_TTSDisabled_NoAutoSpeak() async throws {
        mockAPI.eventsToYield = [.text("ok"), .done]
        let vm = makeViewModel()
        vm.setTTSEnabled(false)

        vm.send("hi")
        try await Task.sleep(nanoseconds: 300_000_000)

        XCTAssertEqual(mockSpeech.speakCallCount, 0, "ttsEnabled=false 时不应自动播")
    }

    func testSpeak_ManualOverride_PlaysNewText() {
        let vm = makeViewModel()
        vm.speak("测试")
        XCTAssertEqual(mockSpeech.lastSpokenText, "测试")
    }

    func testSetTTSEnabled_ToggleStopsSpeaking() async throws {
        mockSpeech.permissionStatus = .granted
        mockAPI.eventsToYield = [.text("hi"), .done]
        let vm = makeViewModel()
        vm.send("hi")
        try await Task.sleep(nanoseconds: 300_000_000)
        XCTAssertEqual(mockSpeech.state, .speaking)

        vm.setTTSEnabled(false)
        XCTAssertFalse(vm.ttsEnabled)
        XCTAssertEqual(mockSpeech.state, .idle, "关闭 TTS 应停掉正在播的")
    }

    // MARK: - Loop 10.3: characterVoiceId 路径 — speak(_:voiceId:) async

    func testCommitStreamedMessage_WithVoiceId_CallsVoiceIdSpeak() async throws {
        mockAPI.eventsToYield = [.text("火山"), .text("回复"), .done]
        let vm = makeViewModel(voiceId: "BV001_streaming")
        vm.setTTSEnabled(true)

        vm.send("hi")
        try await Task.sleep(nanoseconds: 300_000_000)

        // voiceId 路径走 speak(_:voiceId:) async(不是同步的 speak(_:))
        XCTAssertEqual(mockSpeech.voiceIdSpeakCallCount, 1, "voiceId 路径应调 speak(_:voiceId:)")
        XCTAssertEqual(mockSpeech.lastVoiceId, "BV001_streaming")
        XCTAssertEqual(mockSpeech.lastSpokenText, "火山回复")
    }

    func testCommitStreamedMessage_NoVoiceId_StillCallsPlainSpeak() async throws {
        mockAPI.eventsToYield = [.text("系统"), .text("音"), .done]
        let vm = makeViewModel(voiceId: nil)
        vm.setTTSEnabled(true)

        vm.send("hi")
        try await Task.sleep(nanoseconds: 300_000_000)

        // 无 voiceId → 走原 speak(_:) 同步路径
        XCTAssertEqual(mockSpeech.voiceIdSpeakCallCount, 0, "无 voiceId 不应调 speak(_:voiceId:)")
        XCTAssertEqual(mockSpeech.speakCallCount, 1, "应调 speak(_:)")
    }

    func testSpeakWithVoiceId_PropagatesArgs() async {
        let vm = makeViewModel(voiceId: "BV002_other")
        await vm.speak("走火山", voiceId: "BV002_other")
        XCTAssertEqual(mockSpeech.lastSpokenText, "走火山")
        XCTAssertEqual(mockSpeech.lastVoiceId, "BV002_other")
        XCTAssertEqual(mockSpeech.voiceIdSpeakCallCount, 1)
    }

    // MARK: - Helpers

    private func makeViewModel(voiceId: String? = nil) -> ChatViewModel {
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
            factExtractor: nil,
            summaryGenerator: nil,
            speech: mockSpeech,
            characterVoiceId: voiceId
        )
    }
}

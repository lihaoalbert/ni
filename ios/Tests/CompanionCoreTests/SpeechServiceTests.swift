// SpeechServiceTests — MockSpeechService 状态机 + transcript stream 行为
import XCTest
@testable import CompanionCore

@MainActor
final class SpeechServiceTests: XCTestCase {
    var mock: MockSpeechService!

    override func setUp() {
        super.setUp()
        mock = MockSpeechService()
    }

    // MARK: - 初始状态

    func testInitialState_IsIdle() {
        XCTAssertEqual(mock.state, .idle)
        XCTAssertEqual(mock.permissionStatus, .undetermined)
        XCTAssertEqual(mock.audioLevel, 0)
    }

    // MARK: - 权限

    func testRequestPermissions_Granted() async {
        mock.permissionStatus = .undetermined
        mock.nextPermissionResult = .granted
        let result = await mock.requestPermissionsIfNeeded()
        XCTAssertEqual(result, .granted)
        XCTAssertEqual(mock.permissionStatus, .granted)
        XCTAssertEqual(mock.permissionRequestCount, 1)
    }

    func testRequestPermissions_DeniedPropagates() async {
        mock.nextPermissionResult = .denied
        let result = await mock.requestPermissionsIfNeeded()
        XCTAssertEqual(result, .denied)
    }

    // MARK: - Transcript stream

    func testStartListening_EmitsPartials() async throws {
        mock.permissionStatus = .granted
        mock.scheduledPartials = ["你", "你好", "你好世", "你好世界"]

        let stream = try await mock.startListening()
        mock.simulatePartials()

        var received: [String] = []
        for await partial in stream {
            received.append(partial)
        }
        XCTAssertEqual(received, ["你", "你好", "你好世", "你好世界"])
    }

    func testStopListening_FinishesStream() async throws {
        mock.permissionStatus = .granted
        mock.scheduledPartials = ["hello"]
        let stream = try await mock.startListening()

        // 异步模拟 partial
        Task { @MainActor in mock.simulatePartials() }
        // 收一条
        var first: String? = nil
        for await partial in stream {
            first = partial
            break
        }
        XCTAssertEqual(first, "hello")

        // 停监听
        mock.stopListening()
    }

    func testStartListening_PermissionDenied_Throws() async {
        mock.permissionStatus = .denied
        do {
            _ = try await mock.startListening()
            XCTFail("expected error")
        } catch {
            // 期望的错误
        }
    }

    // MARK: - TTS

    func testSpeak_TransitionsToSpeaking() {
        mock.speak("你好世界")
        XCTAssertEqual(mock.state, .speaking)
        XCTAssertEqual(mock.lastSpokenText, "你好世界")
        XCTAssertEqual(mock.speakCallCount, 1)
    }

    func testSpeak_EmptyText_Ignored() {
        mock.speak("")
        XCTAssertEqual(mock.state, .idle)
        XCTAssertEqual(mock.speakCallCount, 0)
    }

    func testStopSpeaking_ResetsToIdle() {
        mock.speak("hi")
        mock.stopSpeaking()
        XCTAssertEqual(mock.state, .idle)
    }
}

// MARK: - Mock

@MainActor
final class MockSpeechService: SpeechServiceProtocol, @unchecked Sendable {
    var state: SpeechState = .idle
    var permissionStatus: SpeechPermissionStatus = .undetermined
    var audioLevel: Float = 0

    // 控制行为
    var nextPermissionResult: SpeechPermissionStatus = .granted
    var permissionRequestCount = 0
    var scheduledPartials: [String] = []
    var lastSpokenText: String?
    var speakCallCount = 0
    /// Loop 10.3: 带 voiceId 的 speak 调用记录
    var lastVoiceId: String?
    var voiceIdSpeakCallCount = 0

    // transcript stream — 每次 startListening() 新建
    private var currentContinuation: AsyncStream<String>.Continuation?

    func requestPermissionsIfNeeded() async -> SpeechPermissionStatus {
        permissionRequestCount += 1
        // 已决定的状态(granted/denied)保持 — 模拟 iOS 不会弹二次询问
        if permissionStatus != .undetermined {
            return permissionStatus
        }
        permissionStatus = nextPermissionResult
        return nextPermissionResult
    }

    func startListening() async throws -> AsyncStream<String> {
        guard permissionStatus == .granted else {
            state = .error("permission denied")
            throw NSError(domain: "MockSpeech", code: 1, userInfo: [NSLocalizedDescriptionKey: "denied"])
        }
        state = .listening
        let (stream, continuation) = AsyncStream<String>.makeStream()
        currentContinuation = continuation
        return stream
    }

    func stopListening() {
        state = .idle
        audioLevel = 0
        currentContinuation?.finish()
        currentContinuation = nil
    }

    func speak(_ text: String) {
        guard !text.isEmpty else { return }
        lastSpokenText = text
        speakCallCount += 1
        state = .speaking
    }

    func speak(_ text: String, voiceId: String?) async {
        guard !text.isEmpty else { return }
        lastSpokenText = text
        lastVoiceId = voiceId
        voiceIdSpeakCallCount += 1
        speakCallCount += 1
        state = .speaking
    }

    func stopSpeaking() {
        if state == .speaking { state = .idle }
    }

    /// 模拟 SFSpeechRecognizer 不断 emit partial — 真正把 scheduledPartials 喂给 continuation
    func simulatePartials() {
        guard let cont = currentContinuation else { return }
        for p in scheduledPartials {
            cont.yield(p)
            state = .recognizing(p)
        }
        cont.finish()
        currentContinuation = nil
        state = .idle
    }
}

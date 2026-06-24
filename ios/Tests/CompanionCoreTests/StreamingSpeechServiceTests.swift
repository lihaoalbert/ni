// StreamingSpeechServiceTests — 验证 TTS fallback 路径 + 调后端逻辑
// 注:真 AVAudioEngine 播放不能在测试环境跑,所以只测:
//   1) speak(_:) → fallback
//   2) speak(_:voiceId:) with nil/empty → fallback
//   3) speak(_:voiceId:) with valid voiceId → 调 VolcanoTTSClient
//      (后端成功时 AVAudioFile 解析会失败,触发 fallback)
//   4) 后端失败 → fallback
//   5) stopSpeaking 重置状态
import XCTest
@testable import CompanionCore

#if canImport(AVFoundation)
import AVFoundation
#endif

@MainActor
final class StreamingSpeechServiceTests: XCTestCase {
    var mockAPI: MockAPIClient!
    var fallback: MockSpeechService!
    var service: StreamingSpeechService!

    override func setUp() {
        super.setUp()
        mockAPI = MockAPIClient()
        fallback = MockSpeechService()
        service = StreamingSpeechService(api: mockAPI, fallback: fallback)
    }

    // MARK: - speak(_:) — 同步路径,总走 fallback

    func testSpeak_Plain_CallsFallback() {
        service.speak("plain text")
        XCTAssertEqual(fallback.speakCallCount, 1)
        XCTAssertEqual(fallback.lastSpokenText, "plain text")
    }

    func testSpeak_Plain_EmptyIgnored() {
        service.speak("")
        XCTAssertEqual(fallback.speakCallCount, 0)
    }

    // MARK: - speak(_:voiceId:) — 异步路径

    func testSpeakWithVoiceId_Nil_FallsBackToSystem() async {
        await service.speak("hi", voiceId: nil)
        XCTAssertEqual(fallback.speakCallCount, 1)
        XCTAssertEqual(fallback.lastSpokenText, "hi")
        XCTAssertEqual(mockAPI.ttsCallCount, 0, "nil voiceId 不应调后端")
    }

    func testSpeakWithVoiceId_Empty_FallsBackToSystem() async {
        await service.speak("hi", voiceId: "")
        XCTAssertEqual(fallback.speakCallCount, 1)
        XCTAssertEqual(mockAPI.ttsCallCount, 0, "空 voiceId 不应调后端")
    }

    func testSpeakWithVoiceId_BackendFails_FallsBackToSystem() async {
        mockAPI.nextTTSError = APIError.transport(NSError(domain: "test", code: -1))

        await service.speak("retry me", voiceId: "BV001")

        XCTAssertEqual(mockAPI.ttsCallCount, 1, "应先调一次后端")
        XCTAssertEqual(fallback.speakCallCount, 1, "后端失败应 fallback 到系统 TTS")
        XCTAssertEqual(fallback.lastSpokenText, "retry me")
    }

    #if canImport(AVFoundation)
    func testSpeakWithVoiceId_EmptyBytes_FallsBack() async {
        // 后端返回空 bytes → VolcanoTTSError.emptyResponse → fallback
        mockAPI.nextTTSData = Data()
        await service.speak("empty", voiceId: "BV001")
        XCTAssertEqual(fallback.speakCallCount, 1, "空 bytes 也应 fallback")
    }

    func testSpeakWithVoiceId_InvalidMP3_FallsBack() async {
        // 后端返回非 MP3 bytes → AVAudioFile 解析失败 → fallback
        mockAPI.nextTTSData = Data([0x00, 0x01, 0x02, 0x03])
        await service.speak("bad mp3", voiceId: "BV001")
        // 不管 AVAudioFile 解析是否抛,都应 fallback
        XCTAssertGreaterThanOrEqual(fallback.speakCallCount, 1, "无效 MP3 应 fallback")
    }
    #endif

    // MARK: - stopSpeaking

    func testStopSpeaking_ResetsState() {
        fallback.state = .speaking
        service.stopSpeaking()
        XCTAssertEqual(service.state, .idle)
        XCTAssertEqual(fallback.state, .idle, "应同时清 fallback 状态")
    }

    // MARK: - STT 委派给 fallback

    func testRequestPermissions_DelegatesToFallback() async {
        fallback.permissionStatus = .undetermined
        fallback.nextPermissionResult = .granted
        let status = await service.requestPermissionsIfNeeded()
        XCTAssertEqual(status, .granted)
        XCTAssertEqual(fallback.permissionRequestCount, 1)
    }

    func testStartListening_DelegatesToFallback() async throws {
        fallback.permissionStatus = .granted
        fallback.scheduledPartials = ["你好"]
        let stream = try await service.startListening()
        fallback.simulatePartials()
        var received: [String] = []
        for await p in stream { received.append(p) }
        XCTAssertEqual(received, ["你好"])
    }

    func testStopListening_DelegatesToFallback() {
        fallback.state = .listening
        service.stopListening()
        XCTAssertEqual(fallback.state, .idle)
    }
}

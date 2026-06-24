// VolcanoTTSClientTests — 验证 client 调后端 + 错误处理
import XCTest
@testable import CompanionCore

final class VolcanoTTSClientTests: XCTestCase {
    var mockAPI: MockAPIClient!

    override func setUp() {
        super.setUp()
        mockAPI = MockAPIClient()
    }

    func testSynthesize_HappyPath() async throws {
        let payload = Data([0x49, 0x44, 0x33, 0x04, 0x00])  // 假 MP3 bytes
        mockAPI.nextTTSData = payload

        let client = VolcanoTTSClient(api: mockAPI)
        let result = try await client.synthesizeSync(
            text: "你好世界",
            voiceId: "BV001_streaming",
            format: .mp3
        )

        XCTAssertEqual(result, payload)
        XCTAssertEqual(mockAPI.ttsCallCount, 1)
        XCTAssertEqual(mockAPI.lastTTSRequest?.text, "你好世界")
        XCTAssertEqual(mockAPI.lastTTSRequest?.voiceId, "BV001_streaming")
        XCTAssertEqual(mockAPI.lastTTSRequest?.format, .mp3)
    }

    func testSynthesize_EmptyResponse_Throws() async {
        mockAPI.nextTTSData = Data()
        let client = VolcanoTTSClient(api: mockAPI)

        do {
            _ = try await client.synthesizeSync(text: "x", voiceId: "v")
            XCTFail("expected emptyResponse error")
        } catch let e as VolcanoTTSError {
            if case .emptyResponse = e { /* OK */ } else { XCTFail("wrong error: \(e)") }
        } catch {
            XCTFail("wrong error type: \(error)")
        }
    }

    func testSynthesize_PropagatesAPIError() async {
        let underlying = APIError.transport(NSError(domain: "test", code: -1))
        mockAPI.nextTTSError = underlying
        let client = VolcanoTTSClient(api: mockAPI)

        do {
            _ = try await client.synthesizeSync(text: "x", voiceId: "v")
            XCTFail("expected error")
        } catch let e as APIError {
            // VolcanoTTSClient 不包装 — 原样向上抛
            XCTAssertEqual(e.errorDescription, underlying.errorDescription)
        } catch {
            XCTFail("expected APIError, got: \(error)")
        }
    }

    func testSynthesize_NilVoiceId_PassesNil() async throws {
        mockAPI.nextTTSData = Data([0x01])
        let client = VolcanoTTSClient(api: mockAPI)
        _ = try await client.synthesizeSync(text: "hi", voiceId: nil)
        XCTAssertNil(mockAPI.lastTTSRequest?.voiceId)
    }

    func testFormat_Cases() {
        XCTAssertEqual(TTSAudioFormat.mp3.rawValue, "mp3")
        XCTAssertEqual(TTSAudioFormat.wav.rawValue, "wav")
        XCTAssertEqual(TTSAudioFormat.opus.rawValue, "opus")
    }
}

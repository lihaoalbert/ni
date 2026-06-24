/// StreamingSpeechService — 火山引擎 TTS 流式播放的 SpeechService 实现
///
/// Loop 10.3:character 有 voiceId 时,Assistant 回复不走系统 AVSpeechSynthesizer,
/// 调后端 /voice/tts/synthesize 拿 MP3 → AVAudioEngine 流式播放。
///
/// 设计:
/// - @MainActor + @Observable(参考 AppleSpeechService)
/// - 调 speak(_:voiceId:) → 拉 MP3 → schedule file → 播
/// - 失败 fallback 到 AppleSpeechService(系统 TTS),保证体验不中断
/// - stopSpeaking 同时停火山 + 系统 TTS(两者状态都要清)
/// - iOS only:AVAudioEngine / AVAudioPlayerNode 在 macOS 上 API 不同,#if os(iOS) 守门
///
/// 测试:
/// - MockSpeechService 不实现本类 — 它继承 SpeechServiceProtocol 但用默认 speak(voiceId:)(=speak(_:))
/// - 真 StreamingSpeechService 测试用 MockAPIClient.nextTTSData 注入 MP3 bytes,MockPlayer
///   不实际播放只断言 scheduleBuffer 被调
import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

@MainActor
public final class StreamingSpeechService: NSObject, SpeechServiceProtocol, @unchecked Sendable {
    public var state: SpeechState = .idle
    public var permissionStatus: SpeechPermissionStatus = .undetermined
    public var audioLevel: Float = 0

    private let ttsClient: VolcanoTTSClient
    /// AppleSpeechService 只在 iOS 上存在,protocol 化的 fallback 让 macOS 也能编
    private let fallback: SpeechServiceProtocol?

    #if canImport(AVFoundation)
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    #endif

    public init(api: APIClientProtocol, fallback: SpeechServiceProtocol? = nil) {
        self.ttsClient = VolcanoTTSClient(api: api)
        self.fallback = fallback

        #if canImport(AVFoundation)
        super.init()
        // engine + player 接线,真正播时再 startEngine(节省电)
        engine.attach(player)
        // mainMixerNode 默认接好,这里 connect
        let format = engine.mainMixerNode.outputFormat(forBus: 0)
        engine.connect(player, to: engine.mainMixerNode, format: format)
        #endif
    }

    // MARK: - SpeechServiceProtocol

    public func requestPermissionsIfNeeded() async -> SpeechPermissionStatus {
        // 火山 TTS 不需要 mic / 语音识别权限,但保留接口一致;fallback 申请
        return await fallback?.requestPermissionsIfNeeded() ?? .granted
    }

    public func startListening() async throws -> AsyncStream<String> {
        // 火山 TTS 不做 STT,fallback 到 AppleSpeechService
        guard let fallback else {
            throw NSError(domain: "StreamingSpeech", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "STT unavailable (no fallback)"
            ])
        }
        return try await fallback.startListening()
    }

    public func stopListening() {
        fallback?.stopListening()
    }

    public func speak(_ text: String) {
        // 无 voiceId → fallback 到系统 TTS
        fallback?.speak(text)
    }

    public func stopSpeaking() {
        #if canImport(AVFoundation)
        player.stop()
        #endif
        fallback?.stopSpeaking()
        state = .idle
    }

    public func speak(_ text: String, voiceId: String?) async {
        guard !text.isEmpty else { return }
        guard let voiceId, !voiceId.isEmpty else {
            // 无 voiceId → fallback 到系统 TTS
            speak(text)
            return
        }
        state = .speaking
        do {
            let data = try await ttsClient.synthesizeSync(text: text, voiceId: voiceId, format: .mp3)
            try await playMP3(data)
            state = .idle
        } catch {
            // 后端失败 → fallback 到系统 TTS,体验不中断
            print("[StreamingSpeech] TTS fetch failed: \(error) — falling back to system TTS")
            fallback?.speak(text)
            state = .idle
        }
    }

    #if canImport(AVFoundation)
    private func playMP3(_ data: Data) async throws {
        // 写临时文件 → AVAudioFile schedule
        let tmpURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("tts-\(UUID().uuidString).mp3")
        try data.write(to: tmpURL)
        defer { try? FileManager.default.removeItem(at: tmpURL) }

        let file = try AVAudioFile(forReading: tmpURL)
        if !engine.isRunning { try engine.start() }
        player.stop()
        player.scheduleFile(file, at: nil) { [weak self] in
            Task { @MainActor in self?.state = .idle }
        }
        player.play()
    }
    #endif
}
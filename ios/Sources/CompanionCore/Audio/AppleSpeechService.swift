/// AppleSpeechService — 基于 Apple AVFoundation + Speech 的端上语音服务
///
/// TTS:`AVSpeechSynthesizer` + `AVSpeechUtterance`(中文用 system "Tingting" 音)
/// STT:`AVAudioEngine.inputNode` tap → `SFSpeechAudioBufferRecognitionRequest` → 实时 partial
///
/// 关键设计:
/// - 全部 @MainActor(AVAudioEngine / SFSpeechRecognizer 必须在 main thread 配置)
/// - 用 ref-count 的 AudioSessionManager(同 AVAudioSession 共享)
/// - 错误兜底:requiresOnDeviceRecognition 失败 → 退回 false(云端识别)
/// - 60s 自动停止:防止单次录音无限(用户忘了松手)
/// - lifecycle:监听 AVAudioSession.interruptionNotification,被电话打断时停 STT/TTS
///
/// 测试:
/// - 单测用 MockSpeechService 替换,本类只在真机/模拟器手动验证
///
/// iOS only:AVAudioSession / SFSpeechRecognizer / AVAudioApplication 都是 iOS-only;
/// Package.swift 同时支持 macOS 用于测试,所以用 #if os(iOS) 守护
import Foundation
import AVFoundation
#if canImport(Speech)
import Speech
#endif
import Observation

#if os(iOS)
@MainActor
@Observable
public final class AppleSpeechService: NSObject, SpeechServiceProtocol, @unchecked Sendable {
    public private(set) var state: SpeechState = .idle
    public private(set) var permissionStatus: SpeechPermissionStatus = .undetermined
    public private(set) var audioLevel: Float = 0

    // TTS
    private let synthesizer = AVSpeechSynthesizer()
    private var lastSpokenText: String?

    // STT
    private let recognizer: SFSpeechRecognizer?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var audioEngine: AVAudioEngine?
    private var currentStreamContinuation: AsyncStream<String>.Continuation?

    // Lifecycle — `nonisolated(unsafe)` 让 deinit 也能访问
    nonisolated(unsafe) private var interruptionObserver: NSObjectProtocol?
    private let maxListeningDurationSeconds: TimeInterval = 60
    private var listeningTimeoutTask: Task<Void, Never>?

    // Loop 12: TTS-mute-STT 状态 — speak 期间临时停 STT 防回声自问自答
    // - sttWasMutedByTTS: 是否由 TTS 触发的 mute（区别于用户主动 stopListening）
    // - sttTranscriptBeforeMute: mute 前的 transcript（恢复时丢弃，避免播完 TTS 立刻 send 旧 transcript）
    private var sttWasMutedByTTS: Bool = false
    private var sttTranscriptBeforeMute: String?

    public override init() {
        self.recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
        super.init()
        synthesizer.delegate = self
        updateInitialPermissionStatus()
        observeInterruptions()
    }

    deinit {
        // interruptionObserver 通过 [weak self] 捕获,不会 retain self;
        // 这里取出 token 清理 NotificationCenter 的内部表
        if let observer = interruptionObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    // MARK: - Permission

    public func requestPermissionsIfNeeded() async -> SpeechPermissionStatus {
        let speechStatus = await requestSpeechAuthorization()
        let micStatus = await requestMicrophonePermission()
        let combined: SpeechPermissionStatus
        if speechStatus == .denied || micStatus == .denied {
            combined = .denied
        } else if speechStatus == .restricted || micStatus == .restricted {
            combined = .restricted
        } else if speechStatus == .granted && micStatus == .granted {
            combined = .granted
        } else {
            combined = .undetermined
        }
        self.permissionStatus = combined
        return combined
    }

    private func updateInitialPermissionStatus() {
        let speech: SFSpeechRecognizerAuthorizationStatus = SFSpeechRecognizer.authorizationStatus()
        let mic: AVAudioApplication.recordPermission = AVAudioApplication.shared.recordPermission
        switch (speech, mic) {
        case (.authorized, .granted):
            permissionStatus = .granted
        case (.denied, _), (_, .denied):
            permissionStatus = .denied
        case (.restricted, _):
            permissionStatus = .restricted
        default:
            permissionStatus = .undetermined
        }
    }

    private func requestSpeechAuthorization() async -> SpeechPermissionStatus {
        await withCheckedContinuation { (continuation: CheckedContinuation<SpeechPermissionStatus, Never>) in
            SFSpeechRecognizer.requestAuthorization { status in
                let mapped: SpeechPermissionStatus
                switch status {
                case .authorized: mapped = .granted
                case .denied: mapped = .denied
                case .restricted: mapped = .restricted
                case .notDetermined: mapped = .undetermined
                @unknown default: mapped = .undetermined
                }
                continuation.resume(returning: mapped)
            }
        }
    }

    private func requestMicrophonePermission() async -> SpeechPermissionStatus {
        await withCheckedContinuation { (continuation: CheckedContinuation<SpeechPermissionStatus, Never>) in
            AVAudioApplication.requestRecordPermission { granted in
                continuation.resume(returning: granted ? .granted : .denied)
            }
        }
    }

    // MARK: - STT: start / stop

    public func startListening() async throws -> AsyncStream<String> {
        guard permissionStatus == .granted else {
            state = .error("权限未授权")
            throw NSError(
                domain: "AppleSpeechService",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "语音 / 麦克风权限未授权"]
            )
        }
        guard let recognizer, recognizer.isAvailable else {
            state = .error("语音识别不可用")
            throw NSError(
                domain: "AppleSpeechService",
                code: 2,
                userInfo: [NSLocalizedDescriptionKey: "语音识别当前不可用"]
            )
        }

        if recognitionTask != nil {
            stopListening()
        }

        AudioSessionManager.shared.enter(.playAndRecord)

        let audioEngine = AVAudioEngine()
        self.audioEngine = audioEngine
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = false
        if #available(iOS 16.0, *) {
            request.addsPunctuation = true
        }

        let inputNode = audioEngine.inputNode
        let recordingFormat = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
            request.append(buffer)
            if let channelData = buffer.floatChannelData?.pointee {
                let frameLength = Int(buffer.frameLength)
                var sum: Float = 0
                for i in 0..<frameLength {
                    let s = channelData[i]
                    sum += s * s
                }
                let rms = sqrt(sum / Float(max(frameLength, 1)))
                let normalized = min(1.0, rms * 8)
                Task { @MainActor [weak self] in
                    self?.audioLevel = normalized
                }
            }
        }

        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            cleanupAudioEngine()
            AudioSessionManager.shared.leave(.playAndRecord)
            state = .error("音频启动失败")
            throw error
        }

        let (stream, continuation) = AsyncStream<String>.makeStream()
        self.currentStreamContinuation = continuation
        state = .listening

        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if let result {
                    let text = result.bestTranscription.formattedString
                    if !text.isEmpty {
                        self.state = .recognizing(text)
                        continuation.yield(text)
                    }
                    if result.isFinal {
                        continuation.finish()
                        self.currentStreamContinuation = nil
                    }
                }
                if let error {
                    let nsError = error as NSError
                    if nsError.code != 3072 {
                        self.state = .error(error.localizedDescription)
                        continuation.finish()
                        self.currentStreamContinuation = nil
                    }
                }
            }
        }

        listeningTimeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(60 * 1_000_000_000))
            await MainActor.run {
                self?.stopListening()
            }
        }

        return stream
    }

    public func stopListening() {
        listeningTimeoutTask?.cancel()
        listeningTimeoutTask = nil
        cleanupAudioEngine()
        recognitionTask?.cancel()
        recognitionTask = nil
        currentStreamContinuation?.finish()
        currentStreamContinuation = nil
        AudioSessionManager.shared.leave(.playAndRecord)
        if case .listening = state { state = .idle }
        if case .recognizing = state { state = .idle }
    }

    private func cleanupAudioEngine() {
        if let engine = audioEngine {
            let inputNode = engine.inputNode
            inputNode.removeTap(onBus: 0)
            if engine.isRunning {
                engine.stop()
            }
        }
        audioEngine = nil
        audioLevel = 0
    }

    // MARK: - TTS

    public func speak(_ text: String) {
        guard !text.isEmpty else { return }
        if synthesizer.isSpeaking && lastSpokenText == text { return }
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        // TTS 期间 mute STT — 扬声器的声音被 mic 录到会被识别成"用户说话"，
        // 导致 VAD 误判停 + 发，形成自问自答循环。
        // 做法:开始播放时停 recognizer + audioEngine，播完重启（外层 ChatViewModel
        // 持有的 partial transcript 也清零，避免播完后被旧 transcript 触发 stopListeningAndSend）。
        switch state {
        case .listening, .recognizing:
            stopListening()
            sttWasMutedByTTS = true
            sttTranscriptBeforeMute = nil
        default:
            break
        }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
            ?? AVSpeechSynthesisVoice(language: Locale.current.identifier)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        utterance.pitchMultiplier = 1.0
        utterance.volume = 1.0
        AudioSessionManager.shared.enter(.playback)
        lastSpokenText = text
        state = .speaking
        synthesizer.speak(utterance)
    }

    public func stopSpeaking() {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        AudioSessionManager.shared.leave(.playback)
        if case .speaking = state { state = .idle }
        lastSpokenText = nil
    }

    // MARK: - Lifecycle

    private func observeInterruptions() {
        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil,
            queue: .main
        ) { [weak self] note in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.handleInterruption(note)
            }
        }
    }

    private func handleInterruption(_ note: Notification) {
        guard
            let info = note.userInfo,
            let typeRaw = info[AVAudioSessionInterruptionTypeKey] as? UInt,
            let type = AVAudioSession.InterruptionType(rawValue: typeRaw)
        else { return }
        switch type {
        case .began:
            if recognitionTask != nil { stopListening() }
            if synthesizer.isSpeaking { stopSpeaking() }
        case .ended:
            break
        @unknown default:
            break
        }
    }
}

// MARK: - AVSpeechSynthesizerDelegate

extension AppleSpeechService: AVSpeechSynthesizerDelegate {
    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        AudioSessionManager.shared.leave(.playback)
        if case .speaking = state { state = .idle }
        lastSpokenText = nil
        // Loop 12: TTS 播完，若之前是因为 speak mute 了 STT，重启 STT
        // 由 ChatViewModel 通过订阅 state 触发，此处只重置标志位
        sttWasMutedByTTS = false
        sttTranscriptBeforeMute = nil
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        AudioSessionManager.shared.leave(.playback)
        if case .speaking = state { state = .idle }
        lastSpokenText = nil
        sttWasMutedByTTS = false
        sttTranscriptBeforeMute = nil
    }
}
#endif  // os(iOS)

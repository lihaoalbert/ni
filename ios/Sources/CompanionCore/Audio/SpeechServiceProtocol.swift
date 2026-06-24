/// SpeechServiceProtocol — 端上语音服务抽象
///
/// 设计:
/// - App 启动时由 AppState 创建一个 AppleSpeechService(iOS) 或 nil(macOS test)
/// - ChatViewModel 接收可选依赖,nil 时 voice 按钮不显示
/// - transcriptStream: 每次 startListening() 新建一个,stopListening() 后 stream 自动 finish
/// - speak(_:): 异步朗读,自动处理 overlap(在播就停旧的播新的)
/// - requestPermissionsIfNeeded: 一次申请 mic + 语音识别
///
/// Loop 10.3 加:
/// - 可选 speak(_:voiceId:) 方法 — 有 voiceId 时走火山引擎流式,无时走系统 AVSpeechSynthesizer
/// - 默认实现走 speak(_:),兼容 Loop 9 的 AppleSpeechService / MockSpeechService
///
/// 测试:
/// - MockSpeechService(测试用)实现此 protocol,模拟 transcript emit + 状态转移
/// - StreamingSpeechService(Loop 10.3)实现此 protocol + 调火山引擎
import Foundation

public protocol SpeechServiceProtocol: AnyObject, Sendable {
    var state: SpeechState { get }
    var permissionStatus: SpeechPermissionStatus { get }
    /// 录音中实时音量(0-1, RMS),UI 用来做 dB 计量条
    var audioLevel: Float { get }

    /// 一次申请麦克风 + 语音识别权限(iOS 17+ 用 AVAudioApplication / SFSpeechRecognizer 的 async API)
    /// 返回最终状态;任一权限被拒都返回 .denied
    @MainActor
    func requestPermissionsIfNeeded() async -> SpeechPermissionStatus

    /// 开始监听 — 启动 AVAudioEngine + SFSpeechRecognizer
    /// 返回的 AsyncStream 持续 emit partial transcript,stop 时自动 finish
    /// - throws: 权限未授权 / 设备不支持 / AVAudioEngine 启动失败
    @MainActor
    func startListening() async throws -> AsyncStream<String>

    /// 停止监听 — 停止 AVAudioEngine + 取消 recognitionTask
    @MainActor
    func stopListening()

    /// 朗读文字(用 system TTS)
    /// - 在 speak 中再次调 speak 会停掉旧的播新的
    @MainActor
    func speak(_ text: String)

    /// 立刻停 TTS
    @MainActor
    func stopSpeaking()

    /// Loop 10.3: 带 voiceId 的朗读 — 有 voiceId 时子类可重写走后端火山引擎
    /// 默认实现 = 系统 TTS(忽略 voiceId),保证后向兼容 Loop 9
    @MainActor
    func speak(_ text: String, voiceId: String?) async
}

public extension SpeechServiceProtocol {
    @MainActor
    func speak(_ text: String, voiceId: String?) async {
        // 默认实现 — 忽略 voiceId,等同 speak(_:)
        speak(text)
    }
}

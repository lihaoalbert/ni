// SpeechState — 端上语音服务的状态机
// 暴露给 UI(@Observable 跟踪),同时被 ViewModel 用于决策
import Foundation

public enum SpeechState: Equatable, Sendable {
    case idle
    case listening                // 正在录音 + 转写
    case recognizing(String)      // 收到新 partial transcript(关联最新文本,UI 可不绑)
    case speaking                 // TTS 在朗读
    case error(String)
}

public enum SpeechPermissionStatus: Sendable, Equatable {
    case undetermined
    case granted
    case denied                    // 用户拒绝(包括首次拒绝和永久拒绝)
    case restricted                // 家长控制 / MDM 限制
}

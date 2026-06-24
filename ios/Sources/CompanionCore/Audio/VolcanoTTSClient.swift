/// VolcanoTTSClient — 调后端 /voice/tts/synthesize 拿音频 bytes
///
/// Loop 10.3 把 iOS TTS 从系统 AVSpeechSynthesizer 切到后端火山引擎 PCM 流式播放。
/// 后端已存在端点(Loop 5c,backend/app/api/voice.py),iOS 只需调用。
///
/// 设计:
/// - struct(无状态)+ actor 隔离都没必要,每次合成开独立 task 即可
/// - 失败(网络 / 后端 5xx)由调用方决定 fallback(StreamingSpeechService fallback 到 AppleSpeechService)
/// - 30s timeout:网络抖动兜底
/// - format 默认 MP3(移动端 native 支持,iOS 模拟器也能播)
import Foundation

public enum TTSAudioFormat: String, Sendable {
    case mp3
    case wav
    case opus
}

public enum VolcanoTTSError: Error, CustomStringConvertible {
    case emptyResponse
    case httpStatus(Int)
    case timeout
    case noEndpoint

    public var description: String {
        switch self {
        case .emptyResponse: return "TTS response empty"
        case .httpStatus(let s): return "TTS HTTP \(s)"
        case .timeout: return "TTS timeout (30s)"
        case .noEndpoint: return "TTS chat base URL not configured"
        }
    }
}

public struct VolcanoTTSClient: Sendable {
    public let api: APIClientProtocol

    public init(api: APIClientProtocol) {
        self.api = api
    }

    /// 同步版(测试用,直接走 URLSession)— MockAPIClient 用
    public func synthesizeSync(
        text: String,
        voiceId: String?,
        format: TTSAudioFormat = .mp3
    ) async throws -> Data {
        let req = VolcanoTTSRequest(text: text, voiceId: voiceId, format: format)
        let data = try await api.synthesizeTTS(req: req)
        guard !data.isEmpty else { throw VolcanoTTSError.emptyResponse }
        return data
    }
}

public struct VolcanoTTSRequest: Sendable {
    public let text: String
    public let voiceId: String?
    public let format: TTSAudioFormat

    public init(text: String, voiceId: String?, format: TTSAudioFormat) {
        self.text = text
        self.voiceId = voiceId
        self.format = format
    }
}
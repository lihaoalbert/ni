// SSE 事件 — 对应后端 /chat/stream 返回的事件类型
// 后端事件类型: text / tool_use_start / tool_use_input_delta / tool_result / iter_end / done / error
// iOS 端 Loop 6 只关心 text / done / error,其他透传忽略
import Foundation

public struct SSEEvent: Sendable, Equatable {
    public let type: String
    public let text: String?
    public let error: String?
    public let model: String?
    public let iterations: Int?
    public let inputTokens: Int?
    public let outputTokens: Int?

    public var isTerminal: Bool {
        type == "done" || type == "error"
    }

    public var isText: Bool {
        type == "text"
    }
}

// 解码后端事件 JSON(后端 agent runtime 直接 yield 整个 dict,字段是 snake/camel 混合)
public struct SSEServerPayload: Decodable, Sendable {
    let type: String
    let text: String?
    let error: String?
    let model: String?
    let iterations: Int?
    let inputTokens: Int?
    let outputTokens: Int?

    enum CodingKeys: String, CodingKey {
        case type, text, error, model, iterations
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
    }

    public func toEvent() -> SSEEvent {
        SSEEvent(
            type: type,
            text: text,
            error: error,
            model: model,
            iterations: iterations,
            inputTokens: inputTokens,
            outputTokens: outputTokens
        )
    }
}

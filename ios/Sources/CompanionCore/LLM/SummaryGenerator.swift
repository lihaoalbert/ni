/// SummaryGenerator — 滚动摘要生成(端上 LLM)
///
/// 设计:
/// - 输入:最近 N 条消息(默认 20 条,约 10 轮)
/// - 输出:200 字以内、第三人称叙述的对话摘要
/// - 用于 Loop 7 的 Short-term memory,7 天滚动窗口
/// - 调用方控制频率:每完成 10 轮对话触发一次,避免重复生成
import Foundation

public protocol SummaryGeneratorProtocol: Sendable {
    func generate(from messages: [(role: ChatMessage.Role, text: String)]) async throws -> String
}

public final class SummaryGenerator: SummaryGeneratorProtocol, @unchecked Sendable {
    private let llm: OnDeviceLLMServiceProtocol

    public init(llm: OnDeviceLLMServiceProtocol) {
        self.llm = llm
    }

    private static let systemPrompt = """
    你是一个对话摘要助手。基于"对话片段"输出 200 字以内的第三人称摘要。

    要求:
    1. 用中文
    2. 总结用户的主要话题、偏好、关键事件、情感倾向
    3. 用陈述句,不要"用户说"、"AI 回答"这类对话标记
    4. 重点放在用户身上(用户是主角,AI 角色是陪伴者)
    5. 控制在 150-200 字
    6. 不要任何前缀、标题、markdown 围栏
    """

    public func generate(from messages: [(role: ChatMessage.Role, text: String)]) async throws -> String {
        guard !messages.isEmpty else { return "" }

        let prompt = Self.buildPrompt(from: messages)
        let raw = try await llm.generate(
            prompt: prompt,
            systemPrompt: Self.systemPrompt,
            maxTokens: 320,
            temperature: 0.4
        )
        return raw.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func buildPrompt(from messages: [(role: ChatMessage.Role, text: String)]) -> String {
        var lines: [String] = ["对话片段:"]
        for msg in messages {
            let role = msg.role == .user ? "User" : "Assistant"
            lines.append("\(role): \(msg.text)")
        }
        lines.append("")
        lines.append("输出 200 字以内摘要:")
        return lines.joined(separator: "\n")
    }
}

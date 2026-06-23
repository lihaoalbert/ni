/// FactExtractor — 从对话片段抽取结构化事实(端上 LLM)
///
/// 设计:
/// - 输入:最近 N 条 user + assistant 消息(默认 6 条,3 轮)
/// - 系统提示词:明确要求模型输出 JSON 数组,每条 {category, content, confidence}
/// - category 必须从给定的 5 个候选里选(basic / preference / relationship / work / event)
/// - 输出解析:正则从回复里抠 JSON 数组,容忍模型偶尔加 ```json 围栏或多余文字
/// - 失败兜底:解析失败 → 返回空数组,调用方继续聊天流(LLM 抽取是 best-effort)
import Foundation

/// 抽取出的事实(MemoryStore 会转成 FactRecord 落盘)
public struct ExtractedFact: Sendable, Equatable {
    public let category: FactRecord.Category
    public let content: String
    public let confidence: Double

    public init(category: FactRecord.Category, content: String, confidence: Double) {
        self.category = category
        self.content = content
        self.confidence = confidence
    }
}

public protocol FactExtractorProtocol: Sendable {
    /// 从最近对话消息里抽取事实
    /// messages:[(role, text)],role = .user / .assistant
    func extract(from messages: [(role: ChatMessage.Role, text: String)]) async throws -> [ExtractedFact]
}

public final class FactExtractor: FactExtractorProtocol, @unchecked Sendable {
    private let llm: OnDeviceLLMServiceProtocol

    public init(llm: OnDeviceLLMServiceProtocol) {
        self.llm = llm
    }

    private static let systemPrompt = """
    你是一个事实抽取助手。从用户与 AI 的对话片段中,提取关于"用户本人"的结构化事实。

    输出要求:
    1. 仅输出用户的事实(不抽 AI 角色的、不抽虚构剧情里的)
    2. 每条事实用一行 JSON,字段: category / content / confidence
    3. category 必须是下列之一:
       - basic(姓名、性别、年龄、城市等基本身份)
       - preference(喜好、兴趣、风格偏好)
       - relationship(家庭、朋友、伴侣等关系)
       - work(职业、项目、技能、工作内容)
       - event(具体发生过的事、计划要做的事)
    4. confidence 范围 0.0-1.0,根据用户是否明确陈述打分:
       - 用户明确说 → 0.8-1.0
       - 用户暗示或间接提到 → 0.5-0.7
       - 推断但不确定 → 0.3-0.5
       - 不到 0.3 的不要输出
    5. content 用陈述句,10-50 字,不要加"用户"或"TA"作主语,直接陈述事实
       正确:"喜欢爵士乐"
       错误:"用户喜欢爵士乐"
    6. 没有可抽取的事实时,只输出: []
    7. 不要任何解释、不要 markdown 围栏、只输出 JSON 数组

    示例输入对话:
    User: 我叫李明,在杭州做后端开发
    Assistant: 你好李明,杭州是个好地方

    示例输出:
    [{"category":"basic","content":"叫李明","confidence":0.95},{"category":"work","content":"在杭州做后端开发","confidence":0.9}]
    """

    public func extract(from messages: [(role: ChatMessage.Role, text: String)]) async throws -> [ExtractedFact] {
        guard !messages.isEmpty else { return [] }

        let prompt = Self.buildPrompt(from: messages)
        let raw = try await llm.generate(
            prompt: prompt,
            systemPrompt: Self.systemPrompt,
            maxTokens: 512,
            temperature: 0.2  // 低温度,结构化输出更稳定
        )
        return Self.parse(raw)
    }

    // MARK: - Prompt 构造

    private static func buildPrompt(from messages: [(role: ChatMessage.Role, text: String)]) -> String {
        var lines: [String] = ["对话片段:"]
        for msg in messages {
            let role = msg.role == .user ? "User" : "Assistant"
            lines.append("\(role): \(msg.text)")
        }
        lines.append("")
        lines.append("按要求输出 JSON 数组:")
        return lines.joined(separator: "\n")
    }

    // MARK: - Test helpers

    internal static func buildPromptForTesting(from messages: [(role: ChatMessage.Role, text: String)]) -> String {
        buildPrompt(from: messages)
    }

    internal static func parseForTesting(_ raw: String) -> [ExtractedFact] {
        parse(raw)
    }

    // MARK: - 输出解析

    /// 从 LLM 回复抠 JSON 数组 — 容忍 ```json 围栏、前后空白、嵌入文字
    internal static func parse(_ raw: String) -> [ExtractedFact] {
        // 1. 先尝试找 [ ... ] 区间
        guard let arrayRange = findJSONArrayRange(in: raw) else { return [] }
        let jsonString = String(raw[arrayRange])

        // 2. JSONDecoder 解码
        guard let data = jsonString.data(using: .utf8) else { return [] }

        // 用中间类型解码,容忍大小写 / 字段缺失
        struct RawFact: Decodable {
            let category: String
            let content: String
            let confidence: Double?
        }

        let rawFacts: [RawFact]
        do {
            rawFacts = try JSONDecoder().decode([RawFact].self, from: data)
        } catch {
            // 尝试容错:模型可能漏引号、逗号 → 不救,直接返回空
            return []
        }

        // 3. 过滤 + 转 ExtractedFact
        return rawFacts.compactMap { rf in
            let cat = FactRecord.Category(rawValue: rf.category.lowercased()) ?? .basic
            let content = rf.content.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !content.isEmpty else { return nil }
            let conf = max(0, min(1, rf.confidence ?? 0.5))
            guard conf >= 0.3 else { return nil }  // 不到 0.3 的丢弃
            return ExtractedFact(category: cat, content: content, confidence: conf)
        }
    }

    /// 找 JSON 数组的 [ ... ] 区间 — 处理嵌套
    private static func findJSONArrayRange(in text: String) -> Range<String.Index>? {
        guard let startIdx = text.firstIndex(of: "[") else { return nil }
        var depth = 0
        var inString = false
        var escape = false
        var idx = startIdx
        while idx < text.endIndex {
            let ch = text[idx]
            if escape {
                escape = false
                idx = text.index(after: idx)
                continue
            }
            if ch == "\\" && inString {
                escape = true
                idx = text.index(after: idx)
                continue
            }
            if ch == "\"" {
                inString.toggle()
            } else if !inString {
                if ch == "[" {
                    depth += 1
                } else if ch == "]" {
                    depth -= 1
                    if depth == 0 {
                        return startIdx..<text.index(after: idx)
                    }
                }
            }
            idx = text.index(after: idx)
        }
        return nil
    }
}

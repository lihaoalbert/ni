/// Storage 层 Record — 与 API 模型分离
///
/// 原因:
/// - Storage 自己决定时间戳是 Int64 毫秒还是 Date,字段名 / JSON 编码都跟 API 解耦
/// - 后续要换 ORM / schema 不会影响上层(Memory / ChatViewModel)
/// - 测试可独立构造,不必走 JSONDecoder
import Foundation

public struct ConversationRecord: Sendable, Equatable {
    public let id: String
    public let characterId: String
    public let characterName: String
    public let startedAt: Date
    public var lastMessageAt: Date
    public var summary: String?

    public init(
        id: String,
        characterId: String,
        characterName: String,
        startedAt: Date,
        lastMessageAt: Date,
        summary: String? = nil
    ) {
        self.id = id
        self.characterId = characterId
        self.characterName = characterName
        self.startedAt = startedAt
        self.lastMessageAt = lastMessageAt
        self.summary = summary
    }
}

public struct MessageRecord: Sendable, Equatable {
    public enum Role: String, Sendable {
        case user
        case assistant
        case system
    }

    public let id: String
    public let conversationId: String
    public let role: Role
    public let content: String
    /// JSON 编码后的 tool_calls 数组(assistant 可能用到工具)
    public let toolCallsJSON: String?
    /// JSON 编码后的 token_usage { input, output, cached }
    public let tokenUsageJSON: String?
    public let createdAt: Date

    public init(
        id: String,
        conversationId: String,
        role: Role,
        content: String,
        toolCallsJSON: String? = nil,
        tokenUsageJSON: String? = nil,
        createdAt: Date
    ) {
        self.id = id
        self.conversationId = conversationId
        self.role = role
        self.content = content
        self.toolCallsJSON = toolCallsJSON
        self.tokenUsageJSON = tokenUsageJSON
        self.createdAt = createdAt
    }
}

public struct FactRecord: Sendable, Equatable, Identifiable {
    public enum Category: String, Sendable, CaseIterable {
        case basic
        case preference
        case relationship
        case work
        case event
    }

    public let id: String
    public let userId: String
    public var category: Category
    public var content: String
    public var confidence: Double
    public let createdAt: Date
    public var lastAccessedAt: Date
    public var accessCount: Int
    public let sourceMessageId: String?

    public init(
        id: String,
        userId: String,
        category: Category,
        content: String,
        confidence: Double,
        createdAt: Date,
        lastAccessedAt: Date,
        accessCount: Int,
        sourceMessageId: String?
    ) {
        self.id = id
        self.userId = userId
        self.category = category
        self.content = content
        self.confidence = confidence
        self.createdAt = createdAt
        self.lastAccessedAt = lastAccessedAt
        self.accessCount = accessCount
        self.sourceMessageId = sourceMessageId
    }
}

public struct SummaryRecord: Sendable, Equatable {
    public let id: Int64
    public let conversationId: String
    public let summary: String
    public let messageCount: Int
    public let createdAt: Date

    public init(
        id: Int64,
        conversationId: String,
        summary: String,
        messageCount: Int,
        createdAt: Date
    ) {
        self.id = id
        self.conversationId = conversationId
        self.summary = summary
        self.messageCount = messageCount
        self.createdAt = createdAt
    }
}

// 聊天消息 — 本地视图模型,不参与网络序列化
import Foundation

public struct ChatMessage: Identifiable, Hashable, Sendable {
    public let id: UUID
    public let role: Role
    public var text: String
    public let createdAt: Date

    public enum Role: String, Sendable, Hashable {
        case user
        case assistant
    }

    public init(
        id: UUID = UUID(),
        role: Role,
        text: String,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.createdAt = createdAt
    }
}

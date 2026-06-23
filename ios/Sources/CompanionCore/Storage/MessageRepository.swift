/// MessageRepository — 持久化 MessageRecord
///
/// 关键决策:
/// - role 存 enum rawValue(String),DB 层不强制校验(上层保证 user / assistant / system)
/// - toolCallsJSON / tokenUsageJSON 存 raw JSON 字符串(Repository 不解析,留给 Memory / VM)
/// - loadHistory 按 createdAt ASC 排序,limit 兜底(避免一次拉全表)
import Foundation
import SQLite

public final class MessageRepository: @unchecked Sendable {
    private let db: Database

    private let table = Table("messages")
    private let idCol = SQLite.Expression<String>("id")
    private let conversationIdCol = SQLite.Expression<String>("conversation_id")
    private let roleCol = SQLite.Expression<String>("role")
    private let contentCol = SQLite.Expression<String>("content")
    private let toolCallsCol = SQLite.Expression<String?>("tool_calls")
    private let tokenUsageCol = SQLite.Expression<String?>("token_usage")
    private let createdAtCol = SQLite.Expression<Int64>("created_at")

    public init(database: Database) {
        self.db = database
    }

    public func save(_ record: MessageRecord) throws {
        try db.connection.run(table.insert(
            idCol <- record.id,
            conversationIdCol <- record.conversationId,
            roleCol <- record.role.rawValue,
            contentCol <- record.content,
            toolCallsCol <- record.toolCallsJSON,
            tokenUsageCol <- record.tokenUsageJSON,
            createdAtCol <- Self.millis(record.createdAt)
        ))
    }

    public func loadHistory(conversationId: String, limit: Int = 200) throws -> [MessageRecord] {
        let q = table
            .filter(conversationIdCol == conversationId)
            .order(createdAtCol.asc)
            .limit(limit)
        return try db.connection.prepare(q).map(rowToRecord)
    }

    public func count(conversationId: String) throws -> Int {
        let q = table.filter(conversationIdCol == conversationId)
        return try db.connection.scalar(q.count)
    }

    public func deleteAll(conversationId: String) throws {
        let q = table.filter(conversationIdCol == conversationId)
        try db.connection.run(q.delete())
    }

    // MARK: - Mapping

    private func rowToRecord(_ row: Row) -> MessageRecord {
        let roleRaw = row[roleCol]
        let role = MessageRecord.Role(rawValue: roleRaw) ?? .user
        return MessageRecord(
            id: row[idCol],
            conversationId: row[conversationIdCol],
            role: role,
            content: row[contentCol],
            toolCallsJSON: row[toolCallsCol],
            tokenUsageJSON: row[tokenUsageCol],
            createdAt: Date(timeIntervalSince1970: TimeInterval(row[createdAtCol]) / 1000.0)
        )
    }

    private static func millis(_ date: Date) -> Int64 {
        Int64(date.timeIntervalSince1970 * 1000.0)
    }
}

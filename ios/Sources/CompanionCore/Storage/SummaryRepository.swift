/// SummaryRepository — 持久化短时记忆(滚动摘要)
///
/// Loop 7: 不在端上跑 LLM 抽取(Loop 8 端上 LLM 接管)
/// 当前用途:供 ChatViewModel 在显式触发"7 天滚动摘要"时写入;backend 返回的
/// 滚动摘要也可以被 import 进 SQLite,作为下次对话起点。
import Foundation
import SQLite

public final class SummaryRepository: @unchecked Sendable {
    private let db: Database

    private let table = Table("summaries")
    private let idCol = SQLite.Expression<Int64>("id")
    private let conversationIdCol = SQLite.Expression<String>("conversation_id")
    private let summaryCol = SQLite.Expression<String>("summary")
    private let messageCountCol = SQLite.Expression<Int64>("message_count")
    private let createdAtCol = SQLite.Expression<Int64>("created_at")

    public init(database: Database) {
        self.db = database
    }

    @discardableResult
    public func append(conversationId: String, summary: String, messageCount: Int, at now: Date = Date()) throws -> Int64 {
        try db.connection.run(table.insert(
            conversationIdCol <- conversationId,
            summaryCol <- summary,
            messageCountCol <- Int64(messageCount),
            createdAtCol <- Self.millis(now)
        ))
        return db.connection.lastInsertRowid
    }

    /// 最近一次摘要(短时记忆最近状态)
    public func latest(conversationId: String) throws -> SummaryRecord? {
        let q = table
            .filter(conversationIdCol == conversationId)
            .order(createdAtCol.desc, idCol.desc)
            .limit(1)
        return try db.connection.prepare(q).map(rowToRecord).first
    }

    public func list(conversationId: String, limit: Int = 20) throws -> [SummaryRecord] {
        let q = table
            .filter(conversationIdCol == conversationId)
            .order(createdAtCol.desc, idCol.desc)
            .limit(limit)
        return try db.connection.prepare(q).map(rowToRecord)
    }

    public func deleteAll(conversationId: String) throws {
        let q = table.filter(conversationIdCol == conversationId)
        try db.connection.run(q.delete())
    }

    // MARK: - Mapping

    private func rowToRecord(_ row: Row) -> SummaryRecord {
        SummaryRecord(
            id: row[idCol],
            conversationId: row[conversationIdCol],
            summary: row[summaryCol],
            messageCount: Int(row[messageCountCol]),
            createdAt: Date(timeIntervalSince1970: TimeInterval(row[createdAtCol]) / 1000.0)
        )
    }

    private static func millis(_ date: Date) -> Int64 {
        Int64(date.timeIntervalSince1970 * 1000.0)
    }
}

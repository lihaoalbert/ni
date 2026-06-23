/// ConversationRepository — 持久化 ConversationRecord
///
/// 关键决策:
/// - 用 SQLite.swift 的 typed Expression API,字段名只在表定义里写一次
/// - createdAt / lastMessageAt 存 Int64 毫秒(避免 Date 跨时区序列化歧义)
/// - upsertConversation 同时处理新建和已有场景:ChatViewModel 进聊天页时调用一次
import Foundation
import SQLite

public final class ConversationRepository: @unchecked Sendable {
    private let db: Database

    // Table / Column 引用,集中管理
    private let table = Table("conversations")
    private let idCol = SQLite.Expression<String>("id")
    private let characterIdCol = SQLite.Expression<String>("character_id")
    private let characterNameCol = SQLite.Expression<String>("character_name")
    private let startedAtCol = SQLite.Expression<Int64>("started_at")
    private let lastMessageAtCol = SQLite.Expression<Int64>("last_message_at")
    private let summaryCol = SQLite.Expression<String?>("summary")

    public init(database: Database) {
        self.db = database
    }

    /// 不存在则插入,存在则返回已有记录(由调用方按 lastMessageAt 决定要不要 touch)
    public func upsert(
        id: String,
        characterId: String,
        characterName: String,
        now: Date = Date()
    ) throws -> ConversationRecord {
        if let existing = try findById(id) {
            return existing
        }
        let row = ConversationRecord(
            id: id,
            characterId: characterId,
            characterName: characterName,
            startedAt: now,
            lastMessageAt: now
        )
        try db.connection.run(table.insert(
            idCol <- row.id,
            characterIdCol <- row.characterId,
            characterNameCol <- row.characterName,
            startedAtCol <- Self.millis(row.startedAt),
            lastMessageAtCol <- Self.millis(row.lastMessageAt),
            summaryCol <- row.summary
        ))
        return row
    }

    public func findById(_ id: String) throws -> ConversationRecord? {
        let q = table.filter(idCol == id).limit(1)
        return try db.connection.prepare(q).map(rowToRecord).first
    }

    public func listByCharacter(_ characterId: String, limit: Int = 50) throws -> [ConversationRecord] {
        let q = table
            .filter(characterIdCol == characterId)
            .order(lastMessageAtCol.desc)
            .limit(limit)
        return try db.connection.prepare(q).map(rowToRecord)
    }

    public func touchLastMessage(_ id: String, at now: Date = Date()) throws {
        let q = table.filter(idCol == id)
        try db.connection.run(q.update(lastMessageAtCol <- Self.millis(now)))
    }

    public func updateSummary(_ id: String, summary: String?) throws {
        let q = table.filter(idCol == id)
        try db.connection.run(q.update(summaryCol <- summary))
    }

    public func delete(_ id: String) throws {
        let q = table.filter(idCol == id)
        try db.connection.run(q.delete())
    }

    // MARK: - Mapping

    private func rowToRecord(_ row: Row) -> ConversationRecord {
        ConversationRecord(
            id: row[idCol],
            characterId: row[characterIdCol],
            characterName: row[characterNameCol],
            startedAt: Date(timeIntervalSince1970: TimeInterval(row[startedAtCol]) / 1000.0),
            lastMessageAt: Date(timeIntervalSince1970: TimeInterval(row[lastMessageAtCol]) / 1000.0),
            summary: row[summaryCol]
        )
    }

    private static func millis(_ date: Date) -> Int64 {
        Int64(date.timeIntervalSince1970 * 1000.0)
    }
}

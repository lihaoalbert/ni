/// FactRepository — 持久化长期事实(FactRecord)
///
/// 关键决策:
/// - 按 user_id + category 查询,confidence DESC 排序,优先召回高置信度事实
/// - touchAccess 每次召回时调用,confidence 不变但 access_count + 1,last_accessed_at 更新
/// - 后续 Loop 接向量检索时,在 SQL 召回前 100 条后做语义 re-rank
import Foundation
import SQLite

public final class FactRepository: @unchecked Sendable {
    private let db: Database

    private let table = Table("facts")
    private let idCol = SQLite.Expression<String>("id")
    private let userIdCol = SQLite.Expression<String>("user_id")
    private let categoryCol = SQLite.Expression<String>("category")
    private let contentCol = SQLite.Expression<String>("content")
    private let confidenceCol = SQLite.Expression<Double>("confidence")
    private let createdAtCol = SQLite.Expression<Int64>("created_at")
    private let lastAccessedAtCol = SQLite.Expression<Int64>("last_accessed_at")
    private let accessCountCol = SQLite.Expression<Int64>("access_count")
    private let sourceMessageIdCol = SQLite.Expression<String?>("source_message_id")

    public init(database: Database) {
        self.db = database
    }

    public func save(_ record: FactRecord) throws {
        try db.connection.run(table.insert(
            idCol <- record.id,
            userIdCol <- record.userId,
            categoryCol <- record.category.rawValue,
            contentCol <- record.content,
            confidenceCol <- record.confidence,
            createdAtCol <- Self.millis(record.createdAt),
            lastAccessedAtCol <- Self.millis(record.lastAccessedAt),
            accessCountCol <- Int64(record.accessCount),
            sourceMessageIdCol <- record.sourceMessageId
        ))
    }

    public func list(userId: String, category: FactRecord.Category? = nil, limit: Int = 100) throws -> [FactRecord] {
        var q = table.filter(userIdCol == userId)
        if let category {
            q = q.filter(categoryCol == category.rawValue)
        }
        q = q.order(confidenceCol.desc, lastAccessedAtCol.desc).limit(limit)
        return try db.connection.prepare(q).map(rowToRecord)
    }

    public func forget(_ id: String) throws {
        let q = table.filter(idCol == id)
        try db.connection.run(q.delete())
    }

    /// 召回时调用 — last_accessed_at 更新,access_count + 1
    public func touchAccess(_ id: String, at now: Date = Date()) throws {
        let q = table.filter(idCol == id)
        let updated = try db.connection.run(q.update(
            lastAccessedAtCol <- Self.millis(now),
            accessCountCol <- accessCountCol + 1
        ))
        if updated == 0 {
            // 不报错,允许幂等;调用方用 try? 即可
        }
    }

    public func count(userId: String) throws -> Int {
        let q = table.filter(userIdCol == userId)
        return try db.connection.scalar(q.count)
    }

    // MARK: - Mapping

    private func rowToRecord(_ row: Row) -> FactRecord {
        let categoryRaw = row[categoryCol]
        let category = FactRecord.Category(rawValue: categoryRaw) ?? .basic
        return FactRecord(
            id: row[idCol],
            userId: row[userIdCol],
            category: category,
            content: row[contentCol],
            confidence: row[confidenceCol],
            createdAt: Date(timeIntervalSince1970: TimeInterval(row[createdAtCol]) / 1000.0),
            lastAccessedAt: Date(timeIntervalSince1970: TimeInterval(row[lastAccessedAtCol]) / 1000.0),
            accessCount: Int(row[accessCountCol]),
            sourceMessageId: row[sourceMessageIdCol]
        )
    }

    private static func millis(_ date: Date) -> Int64 {
        Int64(date.timeIntervalSince1970 * 1000.0)
    }
}

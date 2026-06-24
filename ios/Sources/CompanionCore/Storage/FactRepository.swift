/// FactRepository — 持久化长期事实(FactRecord)
///
/// 关键决策:
/// - 按 user_id + category 查询,confidence DESC 排序,优先召回高置信度事实
/// - touchAccess 每次召回时调用,confidence 不变但 access_count + 1,last_accessed_at 更新
///
/// Loop 10.1 加:
/// - saveFact 同时异步算 embedding → 灌入 vec0 表 facts_vec
/// - forgetFact 同时从 facts_vec 删行
/// - vectorSearch 用 vec0 KNN(user_id 过滤,按 distance 升序)
/// - 没有 embedding 服务 / DB 没 vec0 → 这些方法静默 no-op 或返回空数组(降级路径)
import Foundation
import SQLite
import CSQLiteVec

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

    /// 注入 embedding 服务(Loop 10.1)— nil 时 vec0 路径不可用
    public init(database: Database, embeddingService: EmbeddingServiceProtocol? = nil) {
        self.db = database
        self.embeddingService = embeddingService
    }

    private let embeddingService: EmbeddingServiceProtocol?

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
        // 异步算 embedding 入 facts_vec(失败 silent — 后续 ListFacts 仍能召回)
        if let svc = embeddingService, db.vectorDimension != nil {
            Task.detached(priority: .utility) { [weak self] in
                await self?.embedAndStore(factId: record.id, content: record.content, service: svc)
            }
        }
    }

    private func embedAndStore(factId: String, content: String, service: EmbeddingServiceProtocol) async {
        let optVec: [Float]? = try? await service.embed(content)
        guard let vec = optVec, !vec.isEmpty else { return }
        try? writeVecRow(factId: factId, vector: vec)
    }

    private func writeVecRow(factId: String, vector: [Float]) throws {
        // 序列化 float[] 为 vec0 接受的 JSON 字符串(e.g. "[0.1, 0.2, ...]")
        let json = "[" + vector.map { String($0) }.joined(separator: ",") + "]"
        // SQLite.swift 没有 bind(?, ?) 形式,我们直接 prepare + step
        let stmt = try db.connection.prepare(
            "INSERT OR REPLACE INTO facts_vec(fact_id, embedding) VALUES (?, ?);"
        )
        // 用 SQLite.swift 的 run 接受 variadic binding
        _ = try stmt.run(factId, json)
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
        // facts_vec 同步清
        if db.vectorDimension != nil {
            try? db.connection.run("DELETE FROM facts_vec WHERE fact_id = ?;", id)
        }
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

    /// Loop 10.1:vec0 KNN 检索 — query 文本先用 embedding 服务算向量,再调 vec0 distance 函数
    /// 距离用 L2(欧式距离);返回 top-K facts JOIN facts_vec,按 distance 升序
    /// 无 embedding 服务 / 无 vec0 → 返回空数组(降级到 substring 路径)
    public func vectorSearch(
        userId: String,
        queryEmbedding: [Float],
        limit: Int
    ) throws -> [(fact: FactRecord, distance: Double)] {
        guard db.vectorDimension != nil else { return [] }
        guard !queryEmbedding.isEmpty else { return [] }
        let json = "[" + queryEmbedding.map { String($0) }.joined(separator: ",") + "]"
        let sql = """
            SELECT f.id, f.user_id, f.category, f.content, f.confidence,
                   f.created_at, f.last_accessed_at, f.access_count, f.source_message_id,
                   v.distance
            FROM facts_vec v
            JOIN facts f ON f.id = v.fact_id
            WHERE v.embedding MATCH ? AND k = ? AND f.user_id = ?
            ORDER BY v.distance ASC;
        """
        var results: [(FactRecord, Double)] = []
        let limitInt64 = Int64(limit)
        for row in try db.connection.prepare(sql, json, limitInt64, userId) {
            // row 是 [Binding?] — 顺序:fields + distance
            let fact = FactRecord(
                id: (row[0] as? String) ?? "",
                userId: (row[1] as? String) ?? "",
                category: FactRecord.Category(rawValue: (row[2] as? String) ?? "basic") ?? .basic,
                content: (row[3] as? String) ?? "",
                confidence: (row[4] as? Double) ?? 0,
                createdAt: Date(timeIntervalSince1970: TimeInterval((row[5] as? Int64) ?? 0) / 1000.0),
                lastAccessedAt: Date(timeIntervalSince1970: TimeInterval((row[6] as? Int64) ?? 0) / 1000.0),
                accessCount: Int((row[7] as? Int64) ?? 0),
                sourceMessageId: row[8] as? String
            )
            let distance = (row[9] as? Double) ?? 0
            results.append((fact, distance))
        }
        return results
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
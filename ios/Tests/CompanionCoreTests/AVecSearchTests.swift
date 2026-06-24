// VectorSearchTests — sqlite-vec KNN 端到端测试
//
// 验证:
// - Database schema v2 启动后建 facts_vec 虚拟表
// - saveFact 触发 embed → facts_vec INSERT
// - vectorSearch 走 vec0 KNN,按 distance 升序返回
// - forgetFact 同步清 facts_vec
// - 无 embedding 服务 → Database.vectorDimension == nil,vectorSearch 返回空数组
import XCTest
@testable import CompanionCore

final class AVecSearchTests: XCTestCase {
    var db: Database?
    var factRepo: FactRepository?
    var mockEmbedding: MockEmbeddingService?

    override func setUp() {
        super.setUp()
        let svc = MockEmbeddingService()
        mockEmbedding = svc
        db = try? Database.inMemory(embeddingService: svc)
        factRepo = db.map { FactRepository(database: $0, embeddingService: svc) }
    }

    override func tearDown() {
        db = nil
        factRepo = nil
        mockEmbedding = nil
        super.tearDown()
    }

    // MARK: - Schema v2

    func testDatabaseV2_FactsVecTableExists() throws {
        let db = try XCTUnwrap(db)
        XCTAssertNotNil(db.vectorDimension)
        XCTAssertEqual(db.vectorDimension, 8)
        // 验证 vec0 module 已加载:能 SELECT vec_version()
        let version = try db.connection.scalar("SELECT vec_version();") as? String
        XCTAssertNotNil(version, "vec_version() 应可用")
        XCTAssertTrue(version?.contains("0.1") ?? false, "version 字符串应包含 0.1.x")
    }

    func testDatabase_NoEmbeddingService_NoFactsVec() throws {
        // 新建一个 db 不带 embedding 服务 — 应跳过 facts_vec 表
        let plainDb = try Database.inMemory()
        XCTAssertNil(plainDb.vectorDimension)
    }

    // MARK: - Save + KNN search

    func testVectorSearch_ReturnsRankedByDistance() async throws {
        guard let db = db, let factRepo = factRepo, let mockEmbedding = mockEmbedding else {
            return XCTFail("setUp 没建好")
        }
        let userId = "u1"
        let now = Date()

        // 3 facts — MockEmbeddingService 按文本 hash 决定向量,不同文本 → 不同向量
        let f1 = FactRecord(
            id: "f1", userId: userId, category: .preference, content: "喜欢爵士乐",
            confidence: 0.9, createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        )
        let f2 = FactRecord(
            id: "f2", userId: userId, category: .work, content: "在星巴克工作",
            confidence: 0.8, createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        )
        let f3 = FactRecord(
            id: "f3", userId: userId, category: .relationship, content: "养了一只橘猫",
            confidence: 0.7, createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        )
        try factRepo.save(f1)
        try factRepo.save(f2)
        try factRepo.save(f3)

        // 等异步 embed 灌入 facts_vec
        try await Task.sleep(nanoseconds: 300_000_000)

        // 用相同文本 query — 应该命中自己(distance=0)
        let queryVec = try await mockEmbedding.embed("喜欢爵士乐") ?? []
        let hits = try factRepo.vectorSearch(userId: userId, queryEmbedding: queryVec, limit: 3)
        XCTAssertEqual(hits.count, 3)
        XCTAssertEqual(hits[0].fact.id, "f1", "同文本应排第一(distance 最小)")
        XCTAssertLessThan(hits[0].distance, hits[1].distance)
        XCTAssertLessThan(hits[1].distance, hits[2].distance)
        _ = db  // silence unused warning
    }

    func testVectorSearch_UserFilterExcludesOthers() async throws {
        guard let factRepo = factRepo, let mockEmbedding = mockEmbedding else {
            return XCTFail("setUp 没建好")
        }
        let now = Date()
        try factRepo.save(FactRecord(
            id: "fa", userId: "userA", category: .basic, content: "苹果",
            confidence: 0.9, createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))
        try factRepo.save(FactRecord(
            id: "fb", userId: "userB", category: .basic, content: "苹果",
            confidence: 0.9, createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))
        try await Task.sleep(nanoseconds: 300_000_000)

        let queryVec = try await mockEmbedding.embed("苹果") ?? []
        let hits = try factRepo.vectorSearch(userId: "userA", queryEmbedding: queryVec, limit: 10)
        XCTAssertEqual(hits.count, 1)
        XCTAssertEqual(hits[0].fact.userId, "userA")
    }

    func testVectorSearch_EmptyDB_ReturnsEmpty() async throws {
        guard let factRepo = factRepo, let mockEmbedding = mockEmbedding else {
            return XCTFail("setUp 没建好")
        }
        let queryVec = try await mockEmbedding.embed("anything") ?? []
        let hits = try factRepo.vectorSearch(userId: "nobody", queryEmbedding: queryVec, limit: 5)
        XCTAssertTrue(hits.isEmpty)
    }

    func testVectorSearch_NoVecTable_ReturnsEmpty() async throws {
        // 没有 embedding 服务的 repo — vectorSearch 应 graceful 返回空
        let plainDb = try Database.inMemory()
        let plainRepo = FactRepository(database: plainDb)
        let hits = try plainRepo.vectorSearch(userId: "u1", queryEmbedding: [1, 2, 3], limit: 5)
        XCTAssertTrue(hits.isEmpty)
    }

    // MARK: - forget 同步清理

    func testForget_RemovesFromVecTable() async throws {
        guard let db = db, let factRepo = factRepo else {
            return XCTFail("setUp 没建好")
        }
        let now = Date()
        try factRepo.save(FactRecord(
            id: "fdel", userId: "u", category: .basic, content: "将被遗忘",
            confidence: 0.5, createdAt: now, lastAccessedAt: now, accessCount: 0, sourceMessageId: nil
        ))
        try await Task.sleep(nanoseconds: 300_000_000)

        // 确认 facts_vec 里有这行
        let beforeCount = try db.connection.scalar(
            "SELECT COUNT(*) FROM facts_vec WHERE fact_id = ?;", "fdel"
        ) as? Int64
        XCTAssertEqual(beforeCount, 1)

        try factRepo.forget("fdel")

        let afterCount = try db.connection.scalar(
            "SELECT COUNT(*) FROM facts_vec WHERE fact_id = ?;", "fdel"
        ) as? Int64
        XCTAssertEqual(afterCount, 0)
    }
}
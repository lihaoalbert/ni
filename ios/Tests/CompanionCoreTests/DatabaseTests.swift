// Database 单元测试 — schema migration + WAL 配置
import XCTest
@testable import CompanionCore

final class DatabaseTests: XCTestCase {
    func testInMemoryOpen() throws {
        let db = try Database.inMemory()
        XCTAssertNotNil(db.connection, "connection should be open")
    }

    func testFilePathOpen() throws {
        // 临时文件路径 — 数据库初始化后会自动创建
        let path = NSTemporaryDirectory() + "db-test-\(UUID().uuidString).sqlite"
        defer { try? FileManager.default.removeItem(atPath: path) }

        let db = try Database(path: path)
        XCTAssertTrue(FileManager.default.fileExists(atPath: path), "file should be created")

        // 关闭后重新打开 → schema 应仍在(PRAGMA user_version = 1)
        let db2 = try Database(path: path)
        let version = try db2.connection.scalar("PRAGMA user_version") as? Int64
        XCTAssertEqual(version, 1, "schema version should persist across opens")
    }

    func testSchemaCreatesTables() throws {
        let db = try Database.inMemory()

        // 验证 4 张表都存在
        let tables = try db.connection.prepare(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).compactMap { row -> String? in
            // row 是 SQLite.swift 的 Statement.Element,我们用 column 索引取值
            (row[0] as? String)
        }

        let expected = ["conversations", "facts", "messages", "summaries"]
        for name in expected {
            XCTAssertTrue(tables.contains(name), "table \(name) should exist, got \(tables)")
        }
    }

    func testIndexesCreated() throws {
        let db = try Database.inMemory()
        let indexes = try db.connection.prepare(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).compactMap { $0[0] as? String }

        // 至少应该看到 5 个索引(conversations / messages / facts / summaries 各 1+,另 unique 自动索引)
        XCTAssertGreaterThan(indexes.count, 3, "expected several indexes, got \(indexes)")
    }

    func testWALEnabled() throws {
        let path = NSTemporaryDirectory() + "wal-test-\(UUID().uuidString).sqlite"
        defer { try? FileManager.default.removeItem(atPath: path) }

        let db = try Database(path: path)
        let mode = try db.connection.scalar("PRAGMA journal_mode") as? String
        XCTAssertEqual(mode?.lowercased(), "wal", "WAL journal mode should be enabled")
    }

    func testForeignKeysEnabled() throws {
        let db = try Database.inMemory()
        let fk = try db.connection.scalar("PRAGMA foreign_keys") as? Int64
        XCTAssertEqual(fk, 1, "foreign_keys should be ON")
    }
}

/// Database — SQLite.swift Connection 封装 + 迁移 + iOS Data Protection
///
/// Loop 7 关键:
/// - 路径外置,工厂函数决定文件位置(测试用 :memory:,生产用 Documents/companion.sqlite)
/// - iOS FileProtectionType.completeUntilFirstUserAuthentication(解锁后第一次解锁设备即永久可读,锁屏不解锁期间密文)
/// - PRAGMA user_version 单调 schema 迁移,初始 v1
/// - WAL 模式:并发读不阻塞写,移动端适合(读多写少)
import Foundation
import SQLite

public enum DatabaseError: Error, CustomStringConvertible {
    case migrationFailed(version: Int, underlying: Error)
    case unsupportedVersion(found: Int64)
    case fileProtectionFailed(path: String, underlying: Error)

    public var description: String {
        switch self {
        case .migrationFailed(let v, let e):
            return "DB migration v\(v) failed: \(e)"
        case .unsupportedVersion(let v):
            return "DB schema version \(v) not supported"
        case .fileProtectionFailed(let p, let e):
            return "set FileProtection on \(p) failed: \(e)"
        }
    }
}

public final class Database: @unchecked Sendable {
    public let connection: Connection

    public let path: String

    public init(path: String) throws {
        self.path = path
        self.connection = try Connection(path)

        // Loop 7: WAL 模式更适合移动端并发场景;foreign_keys 开启保证 messages.conversation_id 引用一致
        try connection.execute("PRAGMA journal_mode = WAL;")
        try connection.execute("PRAGMA foreign_keys = ON;")
        try connection.execute("PRAGMA synchronous = NORMAL;")

        try Self.applyFileProtectionIfNeeded(path: path)
        try migrate()
    }

    /// 内存库(测试用)
    public static func inMemory() throws -> Database {
        try Database(path: ":memory:")
    }

    private static func applyFileProtectionIfNeeded(path: String) throws {
        #if os(iOS)
        // :memory: / *file:* 没有真实路径,跳过
        guard !path.hasPrefix(":") else { return }
        do {
            try FileManager.default.setAttributes(
                [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                ofItemAtPath: path
            )
        } catch {
            throw DatabaseError.fileProtectionFailed(path: path, underlying: error)
        }
        #endif
    }

    // MARK: - Schema Migration

    private func migrate() throws {
        let version = (try? connection.scalar("PRAGMA user_version") as? Int64) ?? 0

        if version < 1 {
            do {
                try migrateV1()
                try connection.execute("PRAGMA user_version = 1;")
            } catch {
                throw DatabaseError.migrationFailed(version: 1, underlying: error)
            }
        }

        // 未来 v2 / v3 在这里加 if version < 2 { ... }; 永远前向
        if version > 1 {
            throw DatabaseError.unsupportedVersion(found: version)
        }
    }

    private func migrateV1() throws {
        // v1 schema:conversations / messages / facts / summaries
        try connection.execute(#"""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            character_name TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            last_message_at INTEGER NOT NULL,
            summary TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_conversations_character
            ON conversations(character_id, last_message_at DESC);

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls TEXT,
            token_usage TEXT,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conv
            ON messages(conversation_id, created_at);

        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            created_at INTEGER NOT NULL,
            last_accessed_at INTEGER NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            source_message_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_facts_user
            ON facts(user_id, category, confidence DESC);

        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_summaries_conv
            ON summaries(conversation_id, created_at DESC);
        """#)
    }
}

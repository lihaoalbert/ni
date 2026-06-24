/// Database — SQLite.swift Connection 封装 + 迁移 + iOS Data Protection
///
/// Loop 7 关键:
/// - 路径外置,工厂函数决定文件位置(测试用 :memory:,生产用 Documents/companion.sqlite)
/// - iOS FileProtectionType.completeUntilFirstUserAuthentication(解锁后第一次解锁设备即永久可读,锁屏不解锁期间密文)
/// - PRAGMA user_version 单调 schema 迁移,初始 v1 → v2 加 sqlite-vec facts_vec 虚拟表
/// - WAL 模式:并发读不阻塞写,移动端适合(读多写少)
///
/// Loop 10.1 加:
/// - sqlite-vec 扩展(CSQLiteVec target)随每个连接 init 时显式 `sqlite3_vec_init(db, NULL, NULL)`
///   (静态链接,无需 dlopen / auto_extension)
/// - schema v2 加 `facts_vec` vec0 虚拟表(每行一个 fact 的 float[N] 向量)
/// - 注入 `EmbeddingServiceProtocol`:dimension != nil 才建 facts_vec,否则跳过 vec0 路径
import Foundation
import SQLite
import SQLite3  // 给 sqlite3 / sqlite3_api_routines 类型;我们的 C entry point 接受 sqlite3 *
import CSQLiteVec

public enum DatabaseError: Error, CustomStringConvertible {
    case migrationFailed(version: Int, underlying: Error)
    case unsupportedVersion(found: Int64)
    case fileProtectionFailed(path: String, underlying: Error)
    case vecExtensionLoadFailed(underlying: Error)

    public var description: String {
        switch self {
        case .migrationFailed(let v, let e):
            return "DB migration v\(v) failed: \(e)"
        case .unsupportedVersion(let v):
            return "DB schema version \(v) not supported"
        case .fileProtectionFailed(let p, let e):
            return "set FileProtection on \(p) failed: \(e)"
        case .vecExtensionLoadFailed(let e):
            return "sqlite-vec extension load failed: \(e)"
        }
    }
}

public final class Database: @unchecked Sendable {
    public let connection: Connection

    public let path: String

    /// vec0 表 schema 声明的 embedding 维度;nil = 未启用向量检索(降级到 substring)
    public let vectorDimension: Int?

    public init(path: String, embeddingService: EmbeddingServiceProtocol? = nil) throws {
        self.path = path
        self.connection = try Connection(path)

        // Loop 7: WAL 模式更适合移动端并发场景;foreign_keys 开启保证 messages.conversation_id 引用一致
        try connection.execute("PRAGMA journal_mode = WAL;")
        try connection.execute("PRAGMA foreign_keys = ON;")
        try connection.execute("PRAGMA synchronous = NORMAL;")

        // 显式加载 sqlite-vec 扩展(静态链接,在 db 上调一次 sqlite3_vec_init 注册 vec0 module)
        // 必须先于 CREATE VIRTUAL TABLE facts_vec,否则 vec0 module unknown
        try Self.loadVecExtension(on: connection)

        // 记录 embedding 维度(若服务可用)— migrateV2 决策是否建 facts_vec 表
        self.vectorDimension = embeddingService?.dimension

        try Self.applyFileProtectionIfNeeded(path: path)
        try migrate(embeddingService: embeddingService)
    }

    /// 内存库(测试用)
    public static func inMemory() throws -> Database {
        try Database(path: ":memory:")
    }

    /// 内存库 + 指定 embedding 服务(用于 vec0 路径单测)
    public static func inMemory(embeddingService: EmbeddingServiceProtocol) throws -> Database {
        try Database(path: ":memory:", embeddingService: embeddingService)
    }

    /// 静态链接的 sqlite-vec 扩展:用我们 C shim 暴露的 `sqlite3_vec_register(void *db)`
/// 在每个新 Connection 上调一次,后续该连接就能识别 vec0 module。
///
/// 为什么不直接调 sqlite3_vec_init:Swift clang importer 把 sqlite3 * 映射成不同类型,
/// 直接传 OpaquePointer 不通过。我们的 shim 接受 void * 即 UnsafeMutableRawPointer?,
/// 避开这个鸿沟。
    private static func loadVecExtension(on connection: Connection) throws {
        let rc = sqlite3_vec_register(UnsafeMutableRawPointer(connection.handle))
        if rc != SQLITE_OK {
            throw DatabaseError.vecExtensionLoadFailed(
                underlying: NSError(domain: "sqlite-vec", code: Int(rc))
            )
        }
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

    private func migrate(embeddingService: EmbeddingServiceProtocol?) throws {
        let version = (try? connection.scalar("PRAGMA user_version") as? Int64) ?? 0

        if version < 1 {
            do {
                try migrateV1()
                try connection.execute("PRAGMA user_version = 1;")
            } catch {
                throw DatabaseError.migrationFailed(version: 1, underlying: error)
            }
        }

        if version < 2 {
            do {
                try migrateV2(embeddingService: embeddingService)
                try connection.execute("PRAGMA user_version = 2;")
            } catch {
                throw DatabaseError.migrationFailed(version: 2, underlying: error)
            }
        }

        if version > 2 {
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

    /// v2 schema:加 sqlite-vec facts_vec 虚拟表(若 embedding 服务可用)
    /// - vec0 表声明维度由 `embeddingService.dimension` 决定
    /// - 没有 service / dimension nil → 跳过 facts_vec,Database.vectorDimension == nil,
    ///   FactRepository 后续 embed save 会 silent no-op
    private func migrateV2(embeddingService: EmbeddingServiceProtocol?) throws {
        guard let dim = embeddingService?.dimension else {
            // 没 embedding 服务 — 跳过 vec0,纯 substring 降级路径
            return
        }
        try connection.execute(#"""
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec USING vec0(
            fact_id TEXT PRIMARY KEY,
            embedding float[\#(dim)]
        );
        """#)
    }
}
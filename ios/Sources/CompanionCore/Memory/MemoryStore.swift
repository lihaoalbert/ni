/// MemoryStore — 4 层记忆的统一接口(ChatViewModel 唯一依赖)
///
/// 设计动机:
/// - 上层(ViewModel / 测试)只看到 4 个分层的方法,不关心实现是 SQLite / 内存 / 远端
/// - 测试用 InMemoryMemoryStore 单测 4 层协议行为;生产用 SQLiteMemoryStore 落盘
/// - 任何层替换(比如 Semantic 换 sqlite-vec)不污染其它层
///
/// 4 层职责:
/// - Working:当前会话上下文,in-memory 缓存(避免每次 send 都查 DB)
/// - ShortTerm:7 天滚动摘要,SQLite 持久化(下次打开 App 还能看到上一段对话的总结)
/// - LongTerm:长期事实(facts),SQLite 持久化(用户偏好、关系、关键事件)
/// - Semantic:语义检索接口骨架,Loop 7 MVP 用 naive in-memory,后续接 sqlite-vec / 真向量
import Foundation

public protocol MemoryStore: Sendable {
    // MARK: - Working(per-conversation in-memory + SQLite 持久化)

    /// 取当前会话已加载的消息(顺序:用户→助手→用户…)
    func workingMessages(conversationId: String) -> [ChatMessage]

    /// 冷启动 / 切换会话时调用,把 SQLite 里的历史重新灌进 Working 缓存
    func hydrateWorking(conversationId: String) throws

    /// 追加一条消息到 in-memory 缓存 + 落 SQLite,ChatViewModel 每次 user send / 收到 assistant
    /// reply 都走这个方法(单一入口)
    func appendAndPersist(conversationId: String, message: ChatMessage) throws

    /// 切换会话 / 主动丢弃时调用
    func clearWorking(conversationId: String)

    // MARK: - Short-term(SQLite 滚动摘要)

    func shortTermSummary(conversationId: String) -> String?

    func saveShortTermSummary(conversationId: String, summary: String, messageCount: Int)

    // MARK: - Long-term(SQLite 事实)

    func saveFact(_ fact: FactRecord)

    func listFacts(userId: String, category: FactRecord.Category?) -> [FactRecord]

    func forgetFact(id: String)

    // MARK: - Semantic(Loop 7 骨架)

    /// 语义检索 — Loop 7 用 in-memory 线性扫描;后续 Loop 替换为真向量检索
    func semanticSearch(userId: String, query: String, limit: Int) -> [FactRecord]
}

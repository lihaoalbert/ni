/// EmbeddingServiceProtocol — 文本 → 向量 的抽象
///
/// 用于语义检索(Semantic Memory layer 4)。
/// Loop 10.1 接入:
/// - 生产实现 `NLEmbeddingService`(Apple NaturalLanguage 端上推理,零成本、零网络)
/// - 测试实现 `MockEmbeddingService`(固定维度,固定规则)
///
/// 调用方:`DefaultMemoryStore` 在 saveFact 后异步算 embedding 灌入 sqlite-vec facts_vec 表;
/// semanticSearch 时用 query embedding 调 vec0 KNN。
///
/// 设计要点:
/// - Sendable:`@unchecked Sendable` 由实现自己保证(Apple API 本身就是 thread-safe)
/// - 异步:`embed(_:)` 是 async 因为 NLEmbedding.vector(for:) 虽然便宜,但 API 设计上可以
///   触发模型 lazy load;mock 实现直接 return 同步包成 async
/// - dimension:nil = 尚未初始化(embed 还没成功调过一次);Database schema v2 启动时检查
///   dimension 与 facts_vec 表的声明维度,不匹配则 rebuild
import Foundation

public protocol EmbeddingServiceProtocol: Sendable {
    /// 文本 → Float32 向量;失败 / 模型不可用 → 返回 nil(降级到 substring 检索)
    func embed(_ text: String) async throws -> [Float]?

    /// 模型输出的向量维度;nil = 尚未初始化
    var dimension: Int? { get }
}
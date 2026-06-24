/// NLEmbeddingService — Apple NaturalLanguage 端上 sentence embedding
///
/// 用 `NLEmbedding.sentenceEmbedding(for: .simplifiedChinese)`(iOS 13+ / macOS 10.15+)拿 sentence vector。
///
/// Apple 文档明确说 sentence embedding "do not support nearest-neighbor search",
/// 这是因为它们的 hash-based index 是 word-level;但 vector 本身是浮点数组,我们自己
/// 喂给 sqlite-vec 做 KNN,绕过这个限制。
///
/// 设计:
/// - 单例(Apple API 鼓励复用,ModelContainer 同理)
/// - 失败 fallback:任何 NLEmbedding 拿不到都返回 nil;MemoryStore 降级回 substring 检索
/// - iOS 17+ / macOS 14+ 才有 .chinese(实测 iOS 18+ 才稳定,iOS 17 不一定有 — fallback 到 nil)
/// - @unchecked Sendable:NLEmbedding 文档保证 thread-safe
///
/// 注:NLEmbedding 在 iOS 模拟器上跑 Metal 时可能 abort(与 MLX 同病);模拟器上
/// AppState 会把 embedding 设为 nil,Database schema 跳过 facts_vec 表创建。
import Foundation
#if canImport(NaturalLanguage)
import NaturalLanguage
#endif

public final class NLEmbeddingService: EmbeddingServiceProtocol, @unchecked Sendable {
    #if canImport(NaturalLanguage)
    private let embedding: NLEmbedding?
    #endif

    public init(language: NLLanguage? = NLLanguage.simplifiedChinese) {
        #if canImport(NaturalLanguage)
        guard let lang = language else {
            self.embedding = nil
            return
        }
        if let s = NLEmbedding.sentenceEmbedding(for: lang) {
            self.embedding = s
        } else if let w = NLEmbedding.wordEmbedding(for: lang) {
            // sentence embedding 不可用 — 退到 word embedding(单句只会有一个词向量)
            self.embedding = w
        } else {
            self.embedding = nil
        }
        #endif
    }

    public var dimension: Int? {
        #if canImport(NaturalLanguage)
        guard let emb = embedding else { return nil }
        return Int(emb.dimension)
        #else
        return nil
        #endif
    }

    public func embed(_ text: String) async throws -> [Float]? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        #if canImport(NaturalLanguage)
        guard let emb = embedding else { return nil }
        let dim = Int(emb.dimension)
        return await Task.detached(priority: .userInitiated) { () -> [Float]? in
            // 1) 优先走 sentence / word 直出(NLEmbedding.vector(for:) 在 Swift 里
            //    暴露为 [Double]? — 手动 cast 到 [Float])
            if let dvec = emb.vector(for: trimmed) {
                return dvec.map { Float($0) }
            }
            // 2) Fallback:NLTokenizer 分词取 word embedding 平均
            var sum = [Float](repeating: 0, count: dim)
            var count = 0
            let tokenizer = NLTokenizer(unit: .word)
            tokenizer.string = trimmed
            tokenizer.enumerateTokens(in: trimmed.startIndex..<trimmed.endIndex) { range, _ in
                let token = String(trimmed[range])
                if let dvec = emb.vector(for: token) {
                    for i in 0..<min(dvec.count, dim) {
                        sum[i] += Float(dvec[i])
                    }
                    count += 1
                }
                return true
            }
            guard count > 0 else { return nil }
            return sum.map { $0 / Float(count) }
        }.value
        #else
        return nil
        #endif
    }
}
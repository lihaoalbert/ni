/// MockEmbeddingService — 测试用确定性 embedding
///
/// 不依赖 NLEmbedding,无 GPU / 无 model 也能跑单测。
/// 算法:hash 文本 → 8 维 Float32(简单但稳定,相同文本总得相同向量)。
/// 维度故意写死 8,与 sqlite-vec facts_vec 表声明一致。
import Foundation

public final class MockEmbeddingService: EmbeddingServiceProtocol, @unchecked Sendable {
    public let dimension: Int? = 8

    public init() {}

    public func embed(_ text: String) async throws -> [Float]? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        var vec = [Float](repeating: 0, count: dimension ?? 8)
        // FNV-1a 32-bit hash,each byte 影响一个维度
        var hash: UInt32 = 2166136261
        for byte in trimmed.utf8 {
            hash ^= UInt32(byte)
            hash = hash &* 16777619
        }
        for i in 0..<(dimension ?? 8) {
            // 4 bytes per dim,shift + cast to [-1, 1]
            let v = UInt32(truncatingIfNeeded: hash >> (i * 4))
            vec[i] = Float(v % 200) / 100.0 - 1.0
        }
        return vec
    }
}
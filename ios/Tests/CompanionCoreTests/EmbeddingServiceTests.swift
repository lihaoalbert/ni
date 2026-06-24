// EmbeddingServiceTests — MockEmbeddingService 行为验证
import XCTest
@testable import CompanionCore

final class EmbeddingServiceTests: XCTestCase {
    // MARK: - Mock

    func testMockEmbedding_DimensionIs8() async throws {
        let svc = MockEmbeddingService()
        XCTAssertEqual(svc.dimension, 8)
        let v = try await svc.embed("hello")
        XCTAssertNotNil(v)
        XCTAssertEqual(v?.count, 8)
    }

    func testMockEmbedding_DeterministicForSameText() async throws {
        let svc = MockEmbeddingService()
        let v1 = try await svc.embed("相同的文本")
        let v2 = try await svc.embed("相同的文本")
        XCTAssertEqual(v1, v2)
    }

    func testMockEmbedding_DifferentTextsDiffer() async throws {
        let svc = MockEmbeddingService()
        let v1 = try await svc.embed("文本一")
        let v2 = try await svc.embed("文本二")
        XCTAssertNotEqual(v1, v2)
    }

    func testMockEmbedding_EmptyTextReturnsNil() async throws {
        let svc = MockEmbeddingService()
        let emptyResult = try await svc.embed("")
        let whitespaceResult = try await svc.embed("   ")
        XCTAssertNil(emptyResult)
        XCTAssertNil(whitespaceResult)
    }

    func testMockEmbedding_ValuesInUnitRange() async throws {
        let svc = MockEmbeddingService()
        let v = try await svc.embed("range check") ?? []
        for x in v {
            XCTAssertGreaterThanOrEqual(x, -1.0)
            XCTAssertLessThanOrEqual(x, 1.0)
        }
    }
}
// SummaryGenerator 单元测试 — Prompt 构造 + LLM 集成
import XCTest
@testable import CompanionCore

final class SummaryGeneratorTests: XCTestCase {
    func testGenerate_DelegatesToLLM() async throws {
        let mockLLM = MockOnDeviceLLM()
        mockLLM.nextResponse = "用户在做 AI 陪伴 App,喜欢爵士乐,计划明年做短剧。"

        let generator = SummaryGenerator(llm: mockLLM)
        let summary = try await generator.generate(from: [
            (.user, "我在做 AI 陪伴 App"),
            (.assistant, "好的"),
            (.user, "我喜欢爵士乐"),
        ])

        XCTAssertEqual(summary, "用户在做 AI 陪伴 App,喜欢爵士乐,计划明年做短剧。")
        XCTAssertTrue(mockLLM.lastPrompt?.contains("AI 陪伴 App") ?? false)
        XCTAssertTrue(mockLLM.lastSystemPrompt?.contains("摘要") ?? false)
    }

    func testGenerate_EmptyMessages_ReturnsEmpty() async throws {
        let mockLLM = MockOnDeviceLLM()
        let generator = SummaryGenerator(llm: mockLLM)
        let summary = try await generator.generate(from: [])
        XCTAssertEqual(summary, "")
        XCTAssertNil(mockLLM.lastPrompt, "LLM should not be called with empty messages")
    }

    func testGenerate_StripsWhitespace() async throws {
        let mockLLM = MockOnDeviceLLM()
        mockLLM.nextResponse = "  用户在做 AI 陪伴 App。\n\n  "
        let generator = SummaryGenerator(llm: mockLLM)
        let summary = try await generator.generate(from: [(.user, "我在做 AI 陪伴 App")])
        XCTAssertEqual(summary, "用户在做 AI 陪伴 App。")
    }
}

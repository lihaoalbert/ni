// FactExtractor 单元测试 — 解析逻辑 + Prompt 构造(mock LLM,不真跑模型)
import XCTest
@testable import CompanionCore

final class FactExtractorTests: XCTestCase {
    // MARK: - Prompt 构造

    func testBuildPrompt_IncludesRoleAndText() {
        let messages: [(role: ChatMessage.Role, text: String)] = [
            (.user, "我叫李明"),
            (.assistant, "你好李明"),
        ]
        let prompt = FactExtractor.buildPromptForTesting(from: messages)

        XCTAssertTrue(prompt.contains("User: 我叫李明"))
        XCTAssertTrue(prompt.contains("Assistant: 你好李明"))
        XCTAssertTrue(prompt.contains("对话片段"))
        XCTAssertTrue(prompt.contains("JSON"))
    }

    // MARK: - 解析:正常 JSON

    func testParse_ValidJSON_ReturnsFacts() {
        let raw = """
        [{"category":"basic","content":"叫李明","confidence":0.95}]
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertEqual(facts.count, 1)
        XCTAssertEqual(facts[0].category, .basic)
        XCTAssertEqual(facts[0].content, "叫李明")
        XCTAssertEqual(facts[0].confidence, 0.95)
    }

    func testParse_MultipleFacts_AllReturned() {
        let raw = """
        [
          {"category":"basic","content":"叫李明","confidence":0.95},
          {"category":"work","content":"在杭州做后端开发","confidence":0.9},
          {"category":"preference","content":"喜欢爵士乐","confidence":0.7}
        ]
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertEqual(facts.count, 3)
        XCTAssertEqual(facts.map { $0.category }, [.basic, .work, .preference])
    }

    // MARK: - 解析:容错

    func testParse_FencedJSON_StillParses() {
        let raw = """
        好的,以下是抽取结果:
        ```json
        [{"category":"basic","content":"叫李明","confidence":0.9}]
        ```
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertEqual(facts.count, 1)
        XCTAssertEqual(facts[0].content, "叫李明")
    }

    func testParse_EmptyArray_ReturnsEmpty() {
        let raw = """
        对话中没有可抽取的事实。
        []
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertTrue(facts.isEmpty)
    }

    func testParse_GarbageBeforeJSON_StillParses() {
        let raw = """
        我先想想...
        嗯,应该是这样:
        [{"category":"work","content":"做工程师","confidence":0.85}]
        完毕
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertEqual(facts.count, 1)
        XCTAssertEqual(facts[0].content, "做工程师")
    }

    func testParse_UnknownCategory_DefaultsToBasic() {
        let raw = """
        [{"category":"unknown_cat","content":"x","confidence":0.5}]
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertEqual(facts.count, 1)
        XCTAssertEqual(facts[0].category, .basic)
    }

    func testParse_LowConfidence_Dropped() {
        let raw = """
        [{"category":"basic","content":"可能叫李明","confidence":0.2}]
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertTrue(facts.isEmpty, "confidence < 0.3 should be dropped")
    }

    func testParse_ConfidenceClamped() {
        let raw = """
        [{"category":"basic","content":"x","confidence":1.5}]
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertEqual(facts.count, 1)
        XCTAssertEqual(facts[0].confidence, 1.0, "confidence > 1.0 should be clamped")
    }

    func testParse_EmptyContent_Dropped() {
        let raw = """
        [{"category":"basic","content":"   ","confidence":0.9}]
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertTrue(facts.isEmpty)
    }

    func testParse_NoJSONArray_ReturnsEmpty() {
        let raw = "我没法抽取"
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertTrue(facts.isEmpty)
    }

    func testParse_NestedBrackets_Handled() {
        let raw = """
        [{"category":"preference","content":"喜欢{爵士,蓝调}乐","confidence":0.8}]
        """
        let facts = FactExtractor.parseForTesting(raw)
        XCTAssertEqual(facts.count, 1)
        XCTAssertTrue(facts[0].content.contains("爵士"))
    }

    // MARK: - Mock LLM 集成

    func testExtract_DelegatesToLLMAndPersists() async throws {
        let mockLLM = MockOnDeviceLLM()
        mockLLM.nextResponse = """
        [{"category":"basic","content":"叫李明","confidence":0.9}]
        """

        let extractor = FactExtractor(llm: mockLLM)
        let facts = try await extractor.extract(from: [
            (.user, "我叫李明"),
            (.assistant, "你好"),
        ])
        XCTAssertEqual(facts.count, 1)
        XCTAssertEqual(mockLLM.lastPrompt?.contains("我叫李明"), true)
        XCTAssertEqual(mockLLM.lastSystemPrompt?.contains("事实抽取"), true)
    }

    func testExtract_LLMError_Propagates() async {
        let mockLLM = MockOnDeviceLLM()
        mockLLM.nextError = OnDeviceLLMError.modelNotLoaded

        let extractor = FactExtractor(llm: mockLLM)
        do {
            _ = try await extractor.extract(from: [(.user, "x")])
            XCTFail("expected error")
        } catch {
            // 期望的错误,确认是 modelNotLoaded
            XCTAssertTrue(error is OnDeviceLLMError)
        }
    }
}

// MARK: - Mock LLM

final class MockOnDeviceLLM: OnDeviceLLMServiceProtocol, @unchecked Sendable {
    var nextResponse: String = ""
    var nextError: Error?
    var lastPrompt: String?
    var lastSystemPrompt: String?

    var state: OnDeviceLLMService.State = .ready

    func load(progressHandler: (@Sendable (Double) -> Void)?) async throws {
        state = .ready
    }

    func generate(prompt: String, systemPrompt: String?, maxTokens: Int, temperature: Float) async throws -> String {
        lastPrompt = prompt
        lastSystemPrompt = systemPrompt
        if let nextError { throw nextError }
        return nextResponse
    }
}

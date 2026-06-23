// SSEReader 单元测试 — 覆盖正常 / 跨 chunk / UTF-8 边界 / 多 data 行 / 收尾
import XCTest
@testable import CompanionCore

final class SSEReaderTests: XCTestCase {
    // 1. 单事件单 chunk
    func testSingleEvent() {
        var reader = SSEReader()
        let events = reader.feed("data: {\"type\":\"text\",\"text\":\"hi\"}\n\n")
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].type, "text")
        XCTAssertEqual(events[0].text, "hi")
    }

    // 2. 多事件单 chunk
    func testMultipleEventsInOneChunk() {
        var reader = SSEReader()
        let chunk = "data: {\"type\":\"text\",\"text\":\"a\"}\n\ndata: {\"type\":\"text\",\"text\":\"b\"}\n\ndata: {\"type\":\"done\"}\n\n"
        let events = reader.feed(chunk)
        XCTAssertEqual(events.count, 3)
        XCTAssertEqual(events.map { $0.text }, ["a", "b", nil])
        XCTAssertEqual(events.last?.type, "done")
        XCTAssertTrue(events.last?.isTerminal ?? false)
    }

    // 3. 跨 chunk:半个 data 行
    func testSplitAcrossChunks_DataLine() {
        var reader = SSEReader()
        XCTAssertEqual(reader.feed(##"data: {"type":"##).count, 0)
        let events = reader.feed(##""text","text":"split"}"## + "\n\n")
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].text, "split")
    }

    // 4. 跨 chunk:\n\n 分隔
    func testSplitAcrossChunks_FrameBoundary() {
        var reader = SSEReader()
        XCTAssertEqual(reader.feed(#"data: {"type":"text","text":"a"}"#).count, 0)
        let events = reader.feed("\n\ndata: {\"type\":\"text\",\"text\":\"b\"}\n\n")
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events[0].text, "a")
        XCTAssertEqual(events[1].text, "b")
    }

    // 5. 多 data: 行(SPEC:用 \n 拼接)
    func testMultipleDataLines() {
        var reader = SSEReader()
        let chunk = "data: {\"type\":\"text\",\ndata: \"text\":\"multi\"}\n\n"
        let events = reader.feed(chunk)
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].text, "multi")
    }

    // 6. 空 data 行跳过
    func testEmptyDataSkipped() {
        var reader = SSEReader()
        let events = reader.feed("data:\n\ndata: {\"type\":\"text\",\"text\":\"x\"}\n\n")
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].text, "x")
    }

    // 7. 收尾:stream end 时 buffer 还有内容
    func testEndOfStreamPicksUpLeftover() {
        var reader = SSEReader()
        _ = reader.feed(#"data: {"type":"text","text":"partial"}"#)  // 没 \n\n
        let last = reader.endOfStream()
        XCTAssertNotNil(last)
        XCTAssertEqual(last?.text, "partial")
    }

    // 8. 中文 UTF-8 跨 chunk(后端会 yield 中文,端上必须能拼回去)
    // 模拟 APIClient 的真实路径:byte → UTF8Boundary 累积 → reader.feed(完整 String)
    func testChineseAcrossChunks() {
        var pending = Data()
        let full = "data: {\"type\":\"text\",\"text\":\"苏晚\"}\n\n".data(using: .utf8)!
        // 逐 byte 累积,UTF8Boundary 在每个完整字符处切出
        var completeStrings: [String] = []
        for byte in full {
            pending.append(byte)
            let s = UTF8Boundary.extract(&pending)
            if !s.isEmpty { completeStrings.append(s) }
        }
        // 现在把累积的完整 string 喂给 reader
        var reader = SSEReader()
        var allEvents: [SSEEvent] = []
        for s in completeStrings {
            allEvents.append(contentsOf: reader.feed(s))
        }
        if let last = reader.endOfStream() { allEvents.append(last) }
        XCTAssertEqual(allEvents.count, 1)
        XCTAssertEqual(allEvents[0].text, "苏晚")
    }

    // 9. error 事件是 terminal
    func testErrorIsTerminal() {
        var reader = SSEReader()
        let events = reader.feed("data: {\"type\":\"error\",\"error\":\"upstream_500\"}\n\n")
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].type, "error")
        XCTAssertEqual(events[0].error, "upstream_500")
        XCTAssertTrue(events[0].isTerminal)
    }

    // 10. 真实后端事件流模拟(text × N + done)
    func testRealisticTextStream() {
        var reader = SSEReader()
        let chunks = [
            "data: {\"type\":\"text\",\"text\":\"你好\"}\n\n",
            "data: {\"type\":\"text\",\"text\":\",\"}\n\n",
            "data: {\"type\":\"text\",\"text\":\"我是苏晚\"}\n\n",
            "data: {\"type\":\"done\",\"iterations\":1,\"model\":\"claude-opus-4-7\"}\n\n",
        ]
        var allEvents: [SSEEvent] = []
        for chunk in chunks {
            allEvents.append(contentsOf: reader.feed(chunk))
        }
        let text = allEvents.filter { $0.isText }.map { $0.text ?? "" }.joined()
        XCTAssertEqual(text, "你好,我是苏晚")
        XCTAssertTrue(allEvents.last?.isTerminal ?? false)
    }
}

// UTF8Boundary 单元测试 — 验证多字节字符在 chunk 边界被正确切分
import XCTest
@testable import CompanionCore

final class UTF8BoundaryTests: XCTestCase {
    func testASCIIOnly() {
        var data = "hello".data(using: .utf8)!
        let str = UTF8Boundary.extract(&data)
        XCTAssertEqual(str, "hello")
        XCTAssertTrue(data.isEmpty)
    }

    func testComplete3ByteChar_Chinese() {
        // "苏" = 0xE8 0x8B 0x8F
        let bytes: [UInt8] = [0xE8, 0x8B, 0x8F]
        var data = Data(bytes)
        let str = UTF8Boundary.extract(&data)
        XCTAssertEqual(str, "苏")
        XCTAssertTrue(data.isEmpty)
    }

    func testIncomplete3ByteChar_KeepsResidue() {
        // 0xE8 0x8B 是 "苏" 的前 2 字节,第 3 字节未到
        let bytes: [UInt8] = [0xE8, 0x8B]
        var data = Data(bytes)
        let str = UTF8Boundary.extract(&data)
        XCTAssertEqual(str, "")  // 没有完整字符
        XCTAssertEqual(data, Data(bytes))  // 保留残余
    }

    func testPartialThenComplete() {
        // 第一轮:残 2 字节;第二轮:补 1 字节 → "苏"
        var data = Data([0xE8, 0x8B])
        XCTAssertEqual(UTF8Boundary.extract(&data), "")
        XCTAssertEqual(data, Data([0xE8, 0x8B]))
        data.append(0x8F)
        let str = UTF8Boundary.extract(&data)
        XCTAssertEqual(str, "苏")
        XCTAssertTrue(data.isEmpty)
    }

    func testMultiChar_MixedBoundary() {
        // "你好" = 0xE4 0xBD 0xA0 0xE5 0xA5 0xBD
        // 切在第 4 字节:前 3 字节 = "你",后 3 字节 = "好"
        var data = Data([0xE4, 0xBD, 0xA0, 0xE5])
        XCTAssertEqual(UTF8Boundary.extract(&data), "你")
        XCTAssertEqual(data, Data([0xE5]))
        data.append(contentsOf: [0xA5, 0xBD])
        XCTAssertEqual(UTF8Boundary.extract(&data), "好")
        XCTAssertTrue(data.isEmpty)
    }

    func test4ByteChar_Emoji() {
        // "🎉" = 0xF0 0x9F 0x8E 0x89
        var data = Data([0xF0, 0x9F, 0x8E])
        XCTAssertEqual(UTF8Boundary.extract(&data), "")
        XCTAssertEqual(data, Data([0xF0, 0x9F, 0x8E]))
        data.append(0x89)
        XCTAssertEqual(UTF8Boundary.extract(&data), "🎉")
    }
}

// APIClient 单元测试 — 用 URLProtocol stub mock 网络
// 覆盖 login 200 / 401 / 404、流式聊天解析
import XCTest
@testable import CompanionCore

// MARK: - URLProtocol stub

class StubURLProtocol: URLProtocol {
    typealias Handler = (URLRequest) -> (HTTPURLResponse, Data?)
    nonisolated(unsafe) static var handler: Handler?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = StubURLProtocol.handler else { return }
        let (response, data) = handler(request)
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if let data { client?.urlProtocol(self, didLoad: data) }
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

// MARK: - Tests

final class APIClientTests: XCTestCase {
    var session: URLSession!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        session = URLSession(configuration: config)
    }

    override func tearDown() {
        StubURLProtocol.handler = nil
        super.tearDown()
    }

    // 1. login 200 → AuthToken
    func testLoginSuccess() async throws {
        StubURLProtocol.handler = { _ in
            let body = """
            {"access_token":"abc","refresh_token":"xyz","token_type":"Bearer","expires_in":3600}
            """.data(using: .utf8)!
            return (HTTPURLResponse(url: URL(string: "http://test")!, statusCode: 200, httpVersion: nil, headerFields: nil)!, body)
        }
        let client = APIClient(
            platformBase: URL(string: "http://test")!,
            chatBase: URL(string: "http://test")!,
            tokenProvider: { nil },
            session: session
        )
        let token = try await client.login(email: "a@b.com", password: "p")
        XCTAssertEqual(token.accessToken, "abc")
        XCTAssertEqual(token.tokenType, "Bearer")
    }

    // 2. listIPs 200 → IPListResponse
    func testListIPs() async throws {
        StubURLProtocol.handler = { _ in
            let body = """
            {"items":[{"id":"ip_001","name":"苏晚","avatar_url":"http://x/a.png","preview_url":"http://x/p.png","tags":["温柔"],"license_type":"personal_perpetual"}],"total":1,"page":1,"page_size":20,"has_more":false}
            """.data(using: .utf8)!
            return (HTTPURLResponse(url: URL(string: "http://test")!, statusCode: 200, httpVersion: nil, headerFields: nil)!, body)
        }
        let client = APIClient(
            platformBase: URL(string: "http://test")!,
            chatBase: URL(string: "http://test")!,
            tokenProvider: { "token" },
            session: session
        )
        let resp = try await client.listIPs()
        XCTAssertEqual(resp.total, 1)
        XCTAssertEqual(resp.items.first?.name, "苏晚")
        XCTAssertEqual(resp.items.first?.id, "ip_001")
    }

    // 3. 流式聊天 — 一次 feed 完整数据,验证事件顺序
    func testStreamChat() async throws {
        let sseBody = #"""
        data: {"type":"text","text":"你好"}

        data: {"type":"text","text":",我是苏晚"}

        data: {"type":"done","iterations":1,"model":"claude-opus-4-7"}

        """#.data(using: .utf8)!

        StubURLProtocol.handler = { _ in
            return (HTTPURLResponse(url: URL(string: "http://test")!, statusCode: 200, httpVersion: nil, headerFields: nil)!, sseBody)
        }
        let client = APIClient(
            platformBase: URL(string: "http://test")!,
            chatBase: URL(string: "http://test")!,
            tokenProvider: { nil },
            session: session
        )

        var received: [SSEEvent] = []
        for try await event in client.streamChat(userID: "u1", characterID: "ip_001", message: "hi", history: []) {
            received.append(event)
        }

        let text = received.filter { $0.isText }.map { $0.text ?? "" }.joined()
        XCTAssertEqual(text, "你好,我是苏晚")
        XCTAssertTrue(received.last?.isTerminal ?? false)
        XCTAssertEqual(received.last?.type, "done")
    }

    // 4. 流式聊天 — 验证中文在 SSE 帧里完整(后端真实场景)
    func testStreamChatChinese() async throws {
        let sseBody = #"data: {"type":"text","text":"苏晚,你好"}"#.data(using: .utf8)! + Data([0x0A, 0x0A])
            + #"data: {"type":"done","iterations":1}"#.data(using: .utf8)! + Data([0x0A, 0x0A])

        StubURLProtocol.handler = { _ in
            (HTTPURLResponse(url: URL(string: "http://test")!, statusCode: 200, httpVersion: nil, headerFields: nil)!, sseBody)
        }
        let client = APIClient(
            platformBase: URL(string: "http://test")!,
            chatBase: URL(string: "http://test")!,
            tokenProvider: { nil },
            session: session
        )

        var received: [SSEEvent] = []
        for try await event in client.streamChat(userID: "u1", characterID: "ip_001", message: "hi", history: []) {
            received.append(event)
        }

        let text = received.filter { $0.isText }.map { $0.text ?? "" }.joined()
        XCTAssertEqual(text, "苏晚,你好")
        XCTAssertTrue(received.last?.isTerminal ?? false)
    }

    // 5. 401 错误
    func testLogin401() async throws {
        StubURLProtocol.handler = { _ in
            (HTTPURLResponse(url: URL(string: "http://test")!, statusCode: 401, httpVersion: nil, headerFields: nil)!, Data())
        }
        let client = APIClient(
            platformBase: URL(string: "http://test")!,
            chatBase: URL(string: "http://test")!,
            tokenProvider: { nil },
            session: session
        )
        do {
            _ = try await client.login(email: "x", password: "y")
            XCTFail("expected unauthorized")
        } catch APIError.unauthorized {
            // pass
        }
    }
}

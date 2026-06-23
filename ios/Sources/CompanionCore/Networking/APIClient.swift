// API 客户端 — 登录 / 拉 IP 列表 / 拉详情 / 流式聊天
// 平台侧(ips-mock)用 Bearer 鉴权;聊天后端(backend /chat/stream)无鉴权,只传 user_id
import Foundation

public protocol APIClientProtocol: Sendable {
    func login(email: String, password: String) async throws -> AuthToken
    func listIPs() async throws -> IPListResponse
    func getIP(id: String) async throws -> CharacterDetail
    func streamChat(
        userID: String,
        characterID: String,
        message: String
    ) -> AsyncThrowingStream<SSEEvent, Error>
}

public enum APIError: Error, LocalizedError {
    case invalidURL
    case http(status: Int, body: String)
    case decoding(Error)
    case transport(Error)
    case unauthorized
    case notFound

    public var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid URL"
        case .http(let status, _): return "HTTP \(status)"
        case .decoding: return "Decoding error"
        case .transport: return "Network error"
        case .unauthorized: return "Unauthorized"
        case .notFound: return "Not found"
        }
    }
}

public struct APIClient: APIClientProtocol {
    public let platformBase: URL
    public let chatBase: URL
    public var tokenProvider: @Sendable () -> String?

    private let session: URLSession
    private let decoder: JSONDecoder

    public init(
        platformBase: URL = AppConfig.platformBaseURL,
        chatBase: URL = AppConfig.chatBaseURL,
        tokenProvider: @escaping @Sendable () -> String? = { nil },
        session: URLSession = .shared
    ) {
        self.platformBase = platformBase
        self.chatBase = chatBase
        self.tokenProvider = tokenProvider
        self.session = session
        let dec = JSONDecoder()
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let isoBasic = ISO8601DateFormatter()
        isoBasic.formatOptions = [.withInternetDateTime]
        dec.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let s = try container.decode(String.self)
            if let d = iso.date(from: s) { return d }
            if let d = isoBasic.date(from: s) { return d }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Invalid date: \(s)")
        }
        self.decoder = dec
    }

    // MARK: - Platform (ips-mock)

    public func login(email: String, password: String) async throws -> AuthToken {
        let url = platformBase.appendingPathComponent("/v1/auth/login")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(LoginRequest(email: email, password: password))
        return try await perform(req)
    }

    public func listIPs() async throws -> IPListResponse {
        let url = platformBase.appendingPathComponent("/v1/ips")
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        return try await perform(req, authenticated: true)
    }

    public func getIP(id: String) async throws -> CharacterDetail {
        let url = platformBase.appendingPathComponent("/v1/ips/\(id)")
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        return try await perform(req, authenticated: true)
    }

    // MARK: - Chat backend (SSE)

    public func streamChat(
        userID: String,
        characterID: String,
        message: String
    ) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let url = chatBase.appendingPathComponent("/chat/stream")
                    var req = URLRequest(url: url)
                    req.httpMethod = "POST"
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    req.httpBody = try JSONSerialization.data(withJSONObject: [
                        "user_id": userID,
                        "character_id": characterID,
                        "message": message,
                    ])

                    let (bytes, response) = try await session.bytes(for: req)
                    guard let http = response as? HTTPURLResponse else {
                        throw APIError.transport(URLError(.badServerResponse))
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        throw APIError.http(status: http.statusCode, body: "stream init failed")
                    }

                    var reader = SSEReader()
                    var pending = Data()
                    for try await byte in bytes {
                        if Task.isCancelled { break }
                        pending.append(byte)
                        let str = UTF8Boundary.extract(&pending)
                        if !str.isEmpty {
                            for event in reader.feed(str) {
                                continuation.yield(event)
                            }
                        }
                    }
                    if !pending.isEmpty, let str = String(data: pending, encoding: .utf8) {
                        for event in reader.feed(str) {
                            continuation.yield(event)
                        }
                    }
                    if let last = reader.endOfStream() {
                        continuation.yield(last)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                task.cancel()
            }
        }
    }

    // MARK: - Internal

    private func perform<T: Decodable>(_ request: URLRequest, authenticated: Bool = false) async throws -> T {
        var req = request
        if authenticated, let token = tokenProvider() {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.transport(URLError(.badServerResponse))
        }
        if http.statusCode == 401 { throw APIError.unauthorized }
        if http.statusCode == 404 { throw APIError.notFound }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(status: http.statusCode, body: String(data: data, encoding: .utf8) ?? "")
        }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }
}

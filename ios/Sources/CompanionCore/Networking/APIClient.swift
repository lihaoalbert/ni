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
        message: String,
        history: [HistoryTurn]
    ) -> AsyncThrowingStream<SSEEvent, Error>

    /// Loop 10.3: 调后端 /voice/tts/synthesize 拿音频 bytes
    /// 默认实现走真实 URLSession;MockAPIClient 提供 canned data
    func synthesizeTTS(req: VolcanoTTSRequest) async throws -> Data

    /// Loop 10.3 UI: 拉 TTS provider 状态 — 后端 /voice/tts/info
    /// 不调实际合成,只读 settings;火山凭据缺失时仍 200,configured=false
    func ttsInfo() async throws -> TTSInfo
}

/// Loop 13: 客户端发的对话历史一条 — 从 iOS SQLite 取出,作为 LLM 上下文发给后端
/// 后端无状态,不再自己存历史
public struct HistoryTurn: Codable, Sendable, Equatable {
    public let role: String  // "user" | "assistant"
    public let content: String

    public init(role: String, content: String) {
        self.role = role
        self.content = content
    }
}

/// Loop 10.3 UI: 后端 TTS 状态镜像 — iOS ChatView toolbar badge 用
public struct TTSInfo: Codable, Sendable, Equatable {
    public let provider: String        // "mock" | "volcengine"
    public let configured: Bool        // 凭据是否齐全
    public let defaultVoice: String    // "zh_female_qingxin" 等
    public let endpoint: String        // host 部分,避免泄露完整 URL
    public let cacheBackend: String    // "memory" | "redis"
    public let cacheMaxSize: Int

    enum CodingKeys: String, CodingKey {
        case provider
        case configured
        case defaultVoice = "default_voice"
        case endpoint
        case cacheBackend = "cache_backend"
        case cacheMaxSize = "cache_max_size"
    }
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
        message: String,
        history: [HistoryTurn]
    ) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let url = chatBase.appendingPathComponent("/chat/stream")
                    var req = URLRequest(url: url)
                    req.httpMethod = "POST"
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    let historyPayload = history.map { ["role": $0.role, "content": $0.content] }
                    req.httpBody = try JSONSerialization.data(withJSONObject: [
                        "user_id": userID,
                        "character_id": characterID,
                        "message": message,
                        "history": historyPayload,
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

    // MARK: - Voice backend

    public func synthesizeTTS(req: VolcanoTTSRequest) async throws -> Data {
        let url = chatBase.appendingPathComponent("/voice/tts/synthesize")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 30
        let payload: [String: Any] = [
            "text": req.text,
            "voice_id": req.voiceId as Any,
            "format": req.format.rawValue,
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.transport(URLError(.badServerResponse))
        }
        guard (200..<300).contains(http.statusCode) else {
            throw VolcanoTTSError.httpStatus(http.statusCode)
        }
        guard !data.isEmpty else {
            throw VolcanoTTSError.emptyResponse
        }
        return data
    }

    // MARK: - Voice info (Loop 10.3 UI badge)

    public func ttsInfo() async throws -> TTSInfo {
        let url = chatBase.appendingPathComponent("/voice/tts/info")
        var req = URLRequest(url: url)
        req.httpMethod = "GET"
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 5
        return try await perform(req)
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

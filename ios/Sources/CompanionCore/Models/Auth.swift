// 鉴权 — 登录响应 + token
// 对应 ips-mock POST /v1/auth/login
import Foundation

public struct AuthToken: Codable, Sendable {
    public let accessToken: String
    public let refreshToken: String
    public let tokenType: String
    public let expiresIn: Int

    enum CodingKeys: String, CodingKey {
        case tokenType = "token_type"
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case expiresIn = "expires_in"
    }
}

public struct LoginRequest: Codable, Sendable {
    public let email: String
    public let password: String
}

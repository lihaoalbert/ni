// 全局 App 状态 — token 存储 + 路由
import Foundation
import Observation
import CompanionCore

@MainActor
@Observable
public final class AppState {
    public enum Route: Equatable, Sendable {
        case login
        case ipList
        case chat(characterID: String, characterName: String, avatarURL: URL)
    }

    public var route: Route = .login
    public var token: AuthToken? {
        didSet { saveToken() }
    }

    private let tokenKey = "auth_token_json"
    private let api: APIClient

    public init(api: APIClient = APIClient()) {
        self.api = api
        if let data = UserDefaults.standard.data(forKey: tokenKey),
           let t = try? JSONDecoder().decode(AuthToken.self, from: data) {
            self.token = t
            self.route = .ipList
        }
    }

    public func login(email: String, password: String) async throws {
        let t = try await api.login(email: email, password: password)
        token = t
        route = .ipList
    }

    public func loginAsTest() async {
        // Mock 默认账号,跳过输入界面(Loop 6 简化流程)
        do {
            try await login(email: "test@ni.app", password: "test1234")
        } catch {
            // 失败也跳到列表(后端不可达时也能看 UI)
            route = .ipList
        }
    }

    public func logout() {
        token = nil
        UserDefaults.standard.remove(tokenKey: tokenKey)
        route = .login
    }

    public func openChat(characterID: String, characterName: String, avatarURL: URL) {
        route = .chat(characterID: characterID, characterName: characterName, avatarURL: avatarURL)
    }

    public func backToList() {
        route = .ipList
    }

    public func apiClient() -> APIClient {
        // 每次返回带最新 token 的 client
        let saved = token
        return APIClient(
            platformBase: AppConfig.platformBaseURL,
            chatBase: AppConfig.chatBaseURL,
            tokenProvider: { saved?.accessToken }
        )
    }

    private func saveToken() {
        guard let token, let data = try? JSONEncoder().encode(token) else { return }
        UserDefaults.standard.set(data, forKey: tokenKey)
    }
}

private extension UserDefaults {
    func remove(tokenKey: String) { removeObject(forKey: tokenKey) }
}

// 全局 App 状态 — token 存储 + 路由 + 持久化层 + 端上 LLM
// Loop 7: 持有 Database + MemoryStore,App 全程单例,ChatViewModel 通过这里注入
// Loop 8: 持有 OnDeviceLLMService + FactExtractor + SummaryGenerator;ChatViewModel 通过工厂注入
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
    public let database: Database?
    public let memory: MemoryStore

    // Loop 8: 端上 LLM — 单例服务,App 全程一份
    public let llm: OnDeviceLLMService?
    public let factExtractor: FactExtractor?
    public let summaryGenerator: SummaryGenerator?

    public init(api: APIClient = APIClient()) {
        self.api = api

        // Loop 7: SQLite + 4 层记忆
        let db: Database?
        do {
            db = try Database(path: DatabaseFactory.defaultPath())
        } catch {
            print("[AppState] Database init failed: \(error) — falling back to in-memory")
            db = nil
        }
        self.database = db

        if let db {
            let msgRepo = MessageRepository(database: db)
            let summaryRepo = SummaryRepository(database: db)
            let factRepo = FactRepository(database: db)
            self.memory = DefaultMemoryStore(
                messageRepository: msgRepo,
                summaryRepository: summaryRepo,
                factRepository: factRepo
            )
        } else {
            self.memory = InMemoryMemoryStore()
        }

        // Loop 8: 端上 LLM — Apple Silicon 真机才有 Metal 设备;模拟器上 MLX 会在
        // device.cpp:300 abort(MTLSimDevice 的 architecture() 返回 null,已知 issue)
        // 真机才能跑端上 LLM
        let llmService: OnDeviceLLMService?
        #if arch(arm64) && os(iOS) && !targetEnvironment(simulator)
        llmService = OnDeviceLLMService()
        #else
        llmService = nil
        #endif
        self.llm = llmService
        self.factExtractor = llmService.map { FactExtractor(llm: $0) }
        self.summaryGenerator = llmService.map { SummaryGenerator(llm: $0) }

        if let data = UserDefaults.standard.data(forKey: tokenKey),
           let t = try? JSONDecoder().decode(AuthToken.self, from: data) {
            self.token = t
            self.route = .ipList
        }
    }

    /// 异步触发 LLM 加载 — App 启动后调一次,UI 用 llm.state 显示下载/加载进度
    public func warmupLLM(progressHandler: (@Sendable (Double) -> Void)? = nil) async {
        guard let llm else { return }
        try? await llm.load(progressHandler: progressHandler)
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

    /// 同一 (user, character) 复用 conversationId — 保证多次进同一角色聊天看到的是同一段历史
    public func conversationID(for characterID: String) -> String {
        // 用 localUserID(端云混合架构,客户端生成)+ characterId 拼接;跨设备同步在 Loop 10
        return "conv_\(AppConfig.localUserID)_\(characterID)"
    }

    /// ChatViewModel 工厂 — 进聊天页时调用一次
    public func makeChatViewModel(characterID: String, characterName: String, api: APIClientProtocol) -> ChatViewModel {
        let convID = conversationID(for: characterID)
        let userID = AppConfig.localUserID

        // 保证 conversation 行存在(供 messages.conversation_id 外键)
        if let db = database {
            let convRepo = ConversationRepository(database: db)
            _ = try? convRepo.upsert(
                id: convID,
                characterId: characterID,
                characterName: characterName
            )
        }

        return ChatViewModel(
            characterID: characterID,
            characterName: characterName,
            userID: userID,
            conversationID: convID,
            api: api,
            memory: memory,
            factExtractor: factExtractor,
            summaryGenerator: summaryGenerator
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

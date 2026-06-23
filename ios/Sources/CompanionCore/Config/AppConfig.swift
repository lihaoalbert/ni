// App 配置 — 编译期常量 + Info.plist 读取后端地址
import Foundation

public enum AppConfig {
    /// 后端 chat API base(本地默认:后端跑 8000 端口)
    public static let chatBaseURL: URL = {
        if let str = Bundle.main.object(forInfoDictionaryKey: "CHAT_BASE_URL") as? String,
           let url = URL(string: str) {
            return url
        }
        return URL(string: "http://localhost:8000")!
    }()

    /// ips-mock 平台 base(本地默认:8001 端口)
    public static let platformBaseURL: URL = {
        if let str = Bundle.main.object(forInfoDictionaryKey: "PLATFORM_BASE_URL") as? String,
           let url = URL(string: str) {
            return url
        }
        return URL(string: "http://localhost:8001")!
    }()

    /// 端云混合架构:无服务端账户体系,客户端生成 UUID 作为 user_id
    public static let localUserID: String = {
        if let stored = UserDefaults.standard.string(forKey: "user_id") {
            return stored
        }
        let new = "ios-\(UUID().uuidString.lowercased())"
        UserDefaults.standard.set(new, forKey: "user_id")
        return new
    }()
}

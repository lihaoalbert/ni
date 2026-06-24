// App 入口 — 路由 LoginView / IPListView / ChatView
// Loop 8: 登录后 warmup LLM(异步下载/加载端上模型);ChatView 通过 appState.llm?.state 显示进度
//  注意:不在 App 启动立即 warm,避免未登录用户被下载 1.2GB
// Loop 9: 进 .chat 路由时异步请求语音权限(麦克风 + 语音识别)
import SwiftUI

@main
struct CompanionAIApp: App {
    @State private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .task(id: appState.token?.accessToken) {
                    // token 变化时触发(从 nil → 字符串即"刚登录");已 ready 时 no-op
                    guard appState.token != nil else { return }
                    await appState.warmupLLM()
                }
        }
    }
}

struct RootView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        switch appState.route {
        case .login:
            LoginView(appState: appState)
        case .ipList:
            IPListView(appState: appState)
        case .chat(let id, let name, let avatar):
            NavigationStack {
                ChatView(appState: appState, characterID: id, characterName: name, avatarURL: avatar)
            }
            .task {
                // Loop 9: 第一次进聊天页时申请语音权限(不阻塞 UI)
                await appState.requestSpeechPermissionsIfNeeded()
            }
        }
    }
}

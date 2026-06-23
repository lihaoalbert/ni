// App 入口 — 路由 LoginView / IPListView / ChatView
import SwiftUI

@main
struct CompanionAIApp: App {
    @State private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
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
        }
    }
}

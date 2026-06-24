// IP 列表 — 卡片 + 缩略图,点击进 ChatView
import SwiftUI
import CompanionCore

public struct IPListView: View {
    @State private var viewModel: IPListViewModel
    @Bindable var appState: AppState

    public init(appState: AppState) {
        self.appState = appState
        self._viewModel = State(initialValue: IPListViewModel(api: appState.apiClient()))
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                llmStatusBanner
                content
            }
            .navigationTitle("我的数字人")
            .toolbar {
                ToolbarItem(placement: toolbarTrailing) {
                    Button("登出") { appState.logout() }
                }
            }
            .task { await viewModel.loadIfNeeded() }
            .refreshable { await viewModel.load() }
        }
    }

    /// Loop 8: 端上 LLM 加载状态条 — 首次启动会下载 1.2GB,放在 IPList 顶部
    /// 让用户不必进聊天也能看到加载进度(避免进聊天页后才看到 toolbar 角落的小 badge)
    @ViewBuilder
    private var llmStatusBanner: some View {
        if let llm = appState.llm {
            switch llm.state {
            case .idle, .ready:
                EmptyView()
            case .downloading(let progress):
                statusBanner(icon: "arrow.down.circle", text: "端上 LLM 下载中 \(Int(progress * 100))%", tint: .blue)
            case .loading:
                statusBanner(icon: "gearshape.2", text: "端上 LLM 加载中…", tint: .blue)
            case .error(let msg):
                statusBanner(icon: "exclamationmark.triangle.fill", text: "端上 LLM 失败: \(msg)", tint: .orange)
            }
        }
    }

    private func statusBanner(icon: String, text: String, tint: Color) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).foregroundStyle(tint)
            Text(text).font(.caption2).foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal)
        .padding(.vertical, 6)
        #if os(iOS)
        .background(Color(uiColor: .secondarySystemBackground))
        #else
        .background(Color.gray.opacity(0.1))
        #endif
    }

    private var toolbarTrailing: ToolbarItemPlacement {
        #if os(iOS)
        return .topBarTrailing
        #else
        return .automatic
        #endif
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .idle, .loading:
            ProgressView("加载中…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .loaded(let items):
            List(items) { item in
                Button {
                    appState.openChat(
                        characterID: item.id,
                        characterName: item.name,
                        avatarURL: item.avatarURL,
                        voiceId: item.voiceId
                    )
                } label: {
                    IPCard(item: item)
                }
                .buttonStyle(.plain)
            }
            .listStyle(.plain)
        case .error(let message):
            VStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 40))
                    .foregroundStyle(.orange)
                Text("加载失败").font(.headline)
                Text(message).font(.caption).foregroundStyle(.secondary)
                Button("重试") { Task { await viewModel.load() } }
                    .buttonStyle(.borderedProminent)
            }
            .padding()
        }
    }
}

private struct IPCard: View {
    let item: IPListItem

    var body: some View {
        HStack(spacing: 12) {
            AsyncImage(url: item.avatarURL) { phase in
                switch phase {
                case .success(let img):
                    img.resizable().scaledToFill()
                case .failure:
                    Image(systemName: "person.fill").foregroundStyle(.secondary)
                default:
                    ProgressView()
                }
            }
            .frame(width: 64, height: 64)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text(item.name).font(.headline)
                if let summary = item.personalitySummary {
                    Text(summary).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                HStack(spacing: 4) {
                    ForEach(item.tags.prefix(3), id: \.self) { tag in
                        Text(tag)
                            .font(.caption2)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(.tint.opacity(0.15), in: Capsule())
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }
}

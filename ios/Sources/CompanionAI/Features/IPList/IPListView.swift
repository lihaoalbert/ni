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
            content
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
                    appState.openChat(characterID: item.id, characterName: item.name, avatarURL: item.avatarURL)
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

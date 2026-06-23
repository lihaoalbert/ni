// 聊天页 — 消息气泡 + 输入框 + 流式打字机
import SwiftUI
import CompanionCore

public struct ChatView: View {
    let characterID: String
    let characterName: String
    let avatarURL: URL
    @Bindable var appState: AppState
    @State private var viewModel: ChatViewModel
    @State private var input: String = ""
    @FocusState private var inputFocused: Bool

    public init(appState: AppState, characterID: String, characterName: String, avatarURL: URL) {
        self.appState = appState
        self.characterID = characterID
        self.characterName = characterName
        self.avatarURL = avatarURL
        // Loop 7: 走 AppState 工厂,自动绑定 conversationId + 注入 MemoryStore(冷启动重水合历史)
        self._viewModel = State(initialValue: appState.makeChatViewModel(
            characterID: characterID,
            characterName: characterName,
            api: appState.apiClient()
        ))
    }

    public var body: some View {
        VStack(spacing: 0) {
            messageList
            extractionHint  // Loop 8: 显示"自动记住中…"
            inputBar
        }
        .navigationTitle(characterName)
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            ToolbarItem(placement: toolbarLeading) {
                Button("返回") { appState.backToList() }
            }
            #if os(iOS)
            ToolbarItem(placement: .topBarTrailing) {
                llmStatusBadge
            }
            #endif
        }
    }

    @ViewBuilder
    private var extractionHint: some View {
        if viewModel.isAutoExtracting {
            HStack(spacing: 6) {
                ProgressView().scaleEffect(0.7)
                Text("正在自动记住…")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal)
            .padding(.vertical, 4)
            #if os(iOS)
            .background(Color(uiColor: .secondarySystemBackground))
            #else
            .background(Color.gray.opacity(0.1))
            #endif
        }
    }

    @ViewBuilder
    private var llmStatusBadge: some View {
        let state = appState.llm?.state ?? .idle
        switch state {
        case .idle, .ready:
            EmptyView()
        case .downloading(let p):
            HStack(spacing: 4) {
                ProgressView().scaleEffect(0.6)
                Text("模型 \(Int(p * 100))%")
                    .font(.caption2)
            }
        case .loading:
            HStack(spacing: 4) {
                ProgressView().scaleEffect(0.6)
                Text("加载模型…").font(.caption2)
            }
        case .error(let msg):
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
                .accessibilityLabel("LLM 错误: \(msg)")
        }
    }

    private var toolbarLeading: ToolbarItemPlacement {
        #if os(iOS)
        return .topBarLeading
        #else
        return .automatic
        #endif
    }

    @ViewBuilder
    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(viewModel.messages) { msg in
                        MessageBubble(message: msg, characterName: characterName, avatarURL: avatarURL)
                            .id(msg.id)
                            // Loop 7: 长按 user 消息 → "记住这件事"(显式 save_fact,Loop 8 由端上 LLM 自动抽取)
                            .contextMenu {
                                if msg.role == .user {
                                    Button {
                                        viewModel.saveUserMessageAsFact(msg)
                                    } label: {
                                        Label("记住这件事", systemImage: "bookmark")
                                    }
                                }
                            }
                    }
                    if !viewModel.currentStreamingText.isEmpty {
                        StreamingBubble(text: viewModel.currentStreamingText, characterName: characterName, avatarURL: avatarURL)
                            .id("streaming")
                    }
                    if case .error(let msg) = viewModel.status {
                        Text(msg)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .padding(.horizontal)
                    }
                }
                .padding(.horizontal)
                .padding(.vertical, 8)
            }
            .onChange(of: viewModel.messages.count) { _, _ in
                if let last = viewModel.messages.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
            .onChange(of: viewModel.currentStreamingText) { _, _ in
                withAnimation { proxy.scrollTo("streaming", anchor: .bottom) }
            }
        }
    }

    private var inputBar: some View {
        HStack(spacing: 8) {
            TextField("输入消息…", text: $input, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...4)
                #if os(iOS)
                .focused($inputFocused)
                #endif
                .onSubmit { send() }
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 32))
            }
            .disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isBusy)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.regularMaterial)
    }

    private var isBusy: Bool {
        switch viewModel.status {
        case .sending, .streaming: return true
        default: return false
        }
    }

    private func send() {
        let text = input
        input = ""
        viewModel.send(text)
    }
}

private struct MessageBubble: View {
    let message: ChatMessage
    let characterName: String
    let avatarURL: URL

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if message.role == .assistant { avatar }
            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 2) {
                if message.role == .assistant {
                    Text(characterName).font(.caption2).foregroundStyle(.secondary)
                }
                Text(message.text)
                    .padding(10)
                    .background(bubbleBackground, in: RoundedRectangle(cornerRadius: 14))
                    .foregroundStyle(message.role == .user ? .white : .primary)
            }
            .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
            if message.role == .user { avatar }
        }
    }

    private var avatar: some View {
        AsyncImage(url: avatarURL) { phase in
            switch phase {
            case .success(let img):
                img.resizable().scaledToFill()
            case .failure:
                Image(systemName: "person.fill").foregroundStyle(.secondary)
            default:
                ProgressView()
            }
        }
        .frame(width: 32, height: 32)
        .clipShape(Circle())
    }

    private var bubbleBackground: AnyShapeStyle {
        if message.role == .user {
            return AnyShapeStyle(Color.accentColor)
        }
        #if os(iOS)
        return AnyShapeStyle(Color(uiColor: .secondarySystemBackground))
        #else
        return AnyShapeStyle(Color.gray.opacity(0.15))
        #endif
    }
}

private struct StreamingBubble: View {
    let text: String
    let characterName: String
    let avatarURL: URL

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            AsyncImage(url: avatarURL) { phase in
                switch phase {
                case .success(let img):
                    img.resizable().scaledToFill()
                case .failure:
                    Image(systemName: "person.fill").foregroundStyle(.secondary)
                default:
                    ProgressView()
                }
            }
            .frame(width: 32, height: 32)
            .clipShape(Circle())
            VStack(alignment: .leading, spacing: 2) {
                Text(characterName).font(.caption2).foregroundStyle(.secondary)
                HStack(spacing: 4) {
                    Text(text)
                    ProgressView().scaleEffect(0.6)
                }
                .padding(10)
                .background(secondaryBackground, in: RoundedRectangle(cornerRadius: 14))
            }
            Spacer()
        }
    }

    private var secondaryBackground: Color {
        #if os(iOS)
        return Color(uiColor: .secondarySystemBackground)
        #else
        return Color.gray.opacity(0.15)
        #endif
    }
}

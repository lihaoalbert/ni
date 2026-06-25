// 聊天页 — 消息气泡 + 输入框 + 流式打字机
// Loop 7: 长按 user 消息 → 显式 save_fact
// Loop 8: 端上 LLM 自动抽取 + 顶部 "正在自动记住…" hint
// Loop 9: 按住说话 + 自动 TTS 朗读
//   - inputBar 左侧 mic 按钮(hold-to-talk):按下 startListening,松手 stopListeningAndSend
//   - 监听中显示底部 VoiceInputOverlay:左侧 dB 计量,中间实时 transcript,右侧松手提示
//   - assistant 消息右侧 🔊 按钮:点一下 speak / 再点 stop
//   - toolbar 右侧:LLM 状态 badge + TTS 开关(speaker.wave.2 / slash)
//   - 权限被永久拒 → alert + 引导去设置
import SwiftUI
import CompanionCore
#if canImport(UIKit)
import UIKit
#endif

public struct ChatView: View {
    let characterID: String
    let characterName: String
    let avatarURL: URL
    let voiceId: String?
    @Bindable var appState: AppState
    @State private var viewModel: ChatViewModel
    @State private var input: String = ""
    @FocusState private var inputFocused: Bool
    @State private var showPermissionAlert: Bool = false
    @State private var isMicPressed: Bool = false

    public init(
        appState: AppState,
        characterID: String,
        characterName: String,
        avatarURL: URL,
        voiceId: String? = nil
    ) {
        self.appState = appState
        self.characterID = characterID
        self.characterName = characterName
        self.avatarURL = avatarURL
        self.voiceId = voiceId
        // Loop 7: 走 AppState 工厂,自动绑定 conversationId + 注入 MemoryStore(冷启动重水合历史)
        // Loop 10.3: 传 voiceId → ChatViewModel 走 StreamingSpeechService(火山)而不是系统 TTS
        self._viewModel = State(initialValue: appState.makeChatViewModel(
            characterID: characterID,
            characterName: characterName,
            api: appState.apiClient(),
            characterVoiceId: voiceId
        ))
    }

    public var body: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                messageList
                extractionHint  // Loop 8: 显示"自动记住中…"
                inputBar
            }
            // Loop 9: 监听中浮层
            if viewModel.isListening {
                VoiceInputOverlay(
                    transcript: viewModel.currentListeningTranscript,
                    audioLevel: appState.speech?.audioLevel ?? 0
                )
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: viewModel.isListening)
        .navigationTitle(characterName)
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        // Loop 10.3 UI: 启动时探一次后端 TTS 状态 — toolbar badge 用
        .task {
            await viewModel.probeTTSStatus()
        }
        .toolbar {
            ToolbarItem(placement: toolbarLeading) {
                Button("返回") { appState.backToList() }
            }
            #if os(iOS)
            ToolbarItem(placement: .topBarTrailing) {
                toolbarTrailingItems
            }
            #endif
        }
        .alert("需要麦克风 / 语音识别权限", isPresented: $showPermissionAlert) {
            Button("取消", role: .cancel) {}
            Button("去设置") {
                #if canImport(UIKit)
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
                #endif
            }
        } message: {
            Text("请到 设置 → CompanionAI → 麦克风 / 语音识别 开启权限")
        }
    }

    @ViewBuilder
    private var toolbarTrailingItems: some View {
        HStack(spacing: 12) {
            // Loop 10.3 UI: TTS provider badge — 显示当前 TTS 状态
            ttsStatusBadge
            // TTS 开关
            if appState.speech != nil {
                Button {
                    viewModel.setTTSEnabled(!viewModel.ttsEnabled)
                } label: {
                    Image(systemName: viewModel.ttsEnabled ? "speaker.wave.2.fill" : "speaker.slash.fill")
                        .foregroundStyle(viewModel.ttsEnabled ? .primary : .secondary)
                }
                .accessibilityLabel(viewModel.ttsEnabled ? "关闭朗读" : "开启朗读")
            }
            llmStatusBadge
        }
    }

    /// Loop 10.3 UI: TTS provider 状态 badge
    /// - 火山配齐:绿色 "火山" + 默认音色
    /// - 火山未配:黄色 "Mock"
    /// - 后端不通:红色 "⚠️"
    /// - 未探测:不显示
    @ViewBuilder
    private var ttsStatusBadge: some View {
        switch viewModel.ttsProviderStatus {
        case .unknown:
            EmptyView()
        case .volcengineReady(let voice, _):
            HStack(spacing: 3) {
                Circle().fill(.green).frame(width: 6, height: 6)
                Text("火山")
                    .font(.caption2)
                    .foregroundStyle(.green)
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.green.opacity(0.12), in: Capsule())
            .accessibilityLabel("TTS 火山引擎就绪,音色 \(voice)")
        case .mock, .volcengineNotConfigured:
            HStack(spacing: 3) {
                Circle().fill(.orange).frame(width: 6, height: 6)
                Text("Mock")
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.orange.opacity(0.12), in: Capsule())
            .accessibilityLabel("TTS 走 Mock,火山未配置")
        case .unreachable(let msg):
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
                .font(.caption)
                .accessibilityLabel("TTS 后端不通:\(msg)")
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
                        MessageBubble(
                            message: msg,
                            characterName: characterName,
                            avatarURL: avatarURL,
                            isSpeaking: viewModel.isSpeaking && viewModel.ttsEnabled,
                            onSpeak: { viewModel.speak(msg.text) },
                            onStopSpeaking: { viewModel.stopSpeaking() }
                        )
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

    @ViewBuilder
    private var inputBar: some View {
        HStack(spacing: 8) {
            // Loop 9: mic 按钮(只在 iOS + speech 可用时显示)
            if appState.speech != nil {
                micButton
            }
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

    /// Loop 9: 按住说话按钮 — Button + isPressed 绑定
    private var micButton: some View {
        Button {
            // tap 触发空 — 我们用 HoldButtonStyle 的 isPressed 走生命周期
        } label: {
            Image(systemName: isMicPressed ? "mic.fill" : "mic")
                .font(.system(size: 22))
                .foregroundStyle(isMicPressed ? .red : .accentColor)
                .frame(width: 36, height: 36)
                .background(
                    Circle()
                        .fill(isMicPressed ? Color.red.opacity(0.15) : Color.accentColor.opacity(0.1))
                )
        }
        .buttonStyle(HoldButtonStyle(isPressed: $isMicPressed))
        .onChange(of: isMicPressed) { _, pressed in
            handleMicPressChange(pressed)
        }
        .accessibilityLabel("按住说话")
    }

    private func handleMicPressChange(_ pressed: Bool) {
        if pressed {
            // 按下:启动监听
            Task {
                // 先看权限
                let status = appState.speech?.permissionStatus ?? .undetermined
                if status != .granted {
                    await viewModel.requestSpeechPermissions()
                }
                let after = appState.speech?.permissionStatus ?? .undetermined
                if after == .granted {
                    await viewModel.startListening()
                } else {
                    showPermissionAlert = true
                    isMicPressed = false
                }
            }
        } else {
            // 松手:停 + 发送
            Task { await viewModel.stopListeningAndSend() }
        }
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

// MARK: - HoldButtonStyle

/// ButtonStyle that exposes `isPressed` as a binding
/// iOS 17+ 标准做法 — 替代不可靠的 DragGesture
private struct HoldButtonStyle: ButtonStyle {
    @Binding var isPressed: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.92 : 1.0)
            .animation(.easeInOut(duration: 0.1), value: configuration.isPressed)
            .onChange(of: configuration.isPressed) { _, newValue in
                isPressed = newValue
            }
    }
}

// MARK: - VoiceInputOverlay

/// Loop 9: 监听中底部浮层 — dB 计量 + 实时转写 + 松手提示
private struct VoiceInputOverlay: View {
    let transcript: String
    let audioLevel: Float

    var body: some View {
        VStack(spacing: 8) {
            HStack(alignment: .center, spacing: 12) {
                // dB 计量条
                dBBar
                    .frame(width: 40, height: 24)
                // 实时转写
                Text(transcript.isEmpty ? "请说话…" : transcript)
                    .font(.body)
                    .foregroundStyle(transcript.isEmpty ? .secondary : .primary)
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                // 松手提示
                VStack(spacing: 2) {
                    Image(systemName: "hand.point.up.fill")
                        .font(.system(size: 20))
                        .foregroundStyle(.red)
                    Text("松开发送")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(.regularMaterial)
                    .shadow(color: .black.opacity(0.15), radius: 8, y: 2)
            )
            .padding(.horizontal, 16)
            .padding(.bottom, 80)  // 浮在 inputBar 上方
        }
    }

    private var dBBar: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.secondary.opacity(0.2))
                RoundedRectangle(cornerRadius: 4)
                    .fill(
                        LinearGradient(
                            colors: [.green, .yellow, .red],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(width: geo.size.width * CGFloat(audioLevel))
                    .animation(.easeOut(duration: 0.1), value: audioLevel)
            }
        }
    }
}

private struct MessageBubble: View {
    let message: ChatMessage
    let characterName: String
    let avatarURL: URL
    /// Loop 9: 当前是否有 TTS 在朗读(只对最新一条有意义,但简化全局显示)
    let isSpeaking: Bool
    let onSpeak: () -> Void
    let onStopSpeaking: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if message.role == .assistant { avatar }
            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 2) {
                if message.role == .assistant {
                    Text(characterName).font(.caption2).foregroundStyle(.secondary)
                }
                HStack(alignment: .bottom, spacing: 6) {
                    Text(message.text)
                        .padding(10)
                        .background(bubbleBackground, in: RoundedRectangle(cornerRadius: 14))
                        .foregroundStyle(message.role == .user ? .white : .primary)
                    // Loop 9: assistant 消息的朗读按钮
                    if message.role == .assistant {
                        speakButton
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
            if message.role == .user { avatar }
        }
    }

    /// Loop 9: 单条消息的 TTS 按钮
    @ViewBuilder
    private var speakButton: some View {
        Button {
            // 简化:不管哪条在播,点这个按钮都开新一轮 speak(AppleSpeechService 内有去重)
            // 若当前正在播,先停(这样连点同一个按钮可以 toggle)
            if isSpeaking {
                onStopSpeaking()
            } else {
                onSpeak()
            }
        } label: {
            Image(systemName: isSpeaking ? "stop.fill" : "speaker.wave.2")
                .font(.system(size: 14))
                .foregroundStyle(isSpeaking ? .red : .secondary)
                .frame(width: 24, height: 24)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isSpeaking ? "停止朗读" : "朗读这条消息")
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

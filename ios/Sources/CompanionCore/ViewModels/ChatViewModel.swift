// 聊天 ViewModel — 调 /chat/stream,累积 text delta,处理 done/error
// Loop 7: 接入 MemoryStore — 每次 user send / 收到 assistant reply 都 appendAndPersist 到 SQLite;
//         init 时 hydrateWorking 把历史重新灌进内存,实现"刷新不丢"
import Foundation
import Observation

@MainActor
@Observable
public final class ChatViewModel {
    public enum Status: Sendable, Equatable {
        case idle
        case sending
        case streaming
        case done
        case error(String)
    }

    public private(set) var status: Status = .idle
    public private(set) var messages: [ChatMessage] = []
    public private(set) var currentStreamingText: String = ""
    public let characterID: String
    public let characterName: String
    public let userID: String
    public let conversationID: String

    private let api: APIClientProtocol
    private let memory: MemoryStore
    private var streamTask: Task<Void, Never>?

    public init(
        characterID: String,
        characterName: String,
        userID: String = AppConfig.localUserID,
        conversationID: String,
        api: APIClientProtocol,
        memory: MemoryStore
    ) {
        self.characterID = characterID
        self.characterName = characterName
        self.userID = userID
        self.conversationID = conversationID
        self.api = api
        self.memory = memory
        hydrateFromMemory()
    }

    /// 启动时把历史从 MemoryStore 拉回内存;失败也继续(空历史开始)
    private func hydrateFromMemory() {
        do {
            try memory.hydrateWorking(conversationId: conversationID)
            messages = memory.workingMessages(conversationId: conversationID)
        } catch {
            // 启动失败不应该阻塞 UI — 内存为空即可,后续 send 仍能落盘
            messages = []
        }
    }

    public func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if case .streaming = status { return }
        if case .sending = status { return }

        let userMessage = ChatMessage(role: .user, text: trimmed)
        messages.append(userMessage)
        // 落 SQLite + 更新 in-memory 缓存
        try? memory.appendAndPersist(conversationId: conversationID, message: userMessage)
        currentStreamingText = ""
        status = .sending

        streamTask = Task { [weak self] in
            await self?.runStream(message: trimmed)
        }
    }

    public func cancel() {
        streamTask?.cancel()
        streamTask = nil
        status = .idle
    }

    /// UI 显式触发(Loop 7 不自动抽取 — 端上 LLM 在 Loop 8)
    /// 当前最简策略:把整段消息原文保存为 fact,后续 Loop 8 由端上 LLM 做摘要 + 分类
    public func saveUserMessageAsFact(_ message: ChatMessage, category: FactRecord.Category = .basic) {
        let now = Date()
        let fact = FactRecord(
            id: UUID().uuidString,
            userId: userID,
            category: category,
            content: message.text,
            confidence: 0.6,
            createdAt: now,
            lastAccessedAt: now,
            accessCount: 0,
            sourceMessageId: message.id.uuidString
        )
        memory.saveFact(fact)
    }

    public func forget(factID: String) {
        memory.forgetFact(id: factID)
    }

    private func runStream(message: String) async {
        do {
            status = .streaming
            let stream = api.streamChat(
                userID: userID,
                characterID: characterID,
                message: message
            )
            for try await event in stream {
                handle(event: event)
            }
            // 流结束但可能没收到 done 事件 → 当作 done
            if case .streaming = status {
                commitStreamedMessage()
            }
        } catch let e as APIError {
            if currentStreamingText.isEmpty {
                status = .error(e.errorDescription ?? "Network error")
            } else {
                commitStreamedMessage()
                status = .error(e.errorDescription ?? "Network error")
            }
        } catch {
            status = .error(error.localizedDescription)
        }
    }

    private func handle(event: SSEEvent) {
        if event.isText, let t = event.text {
            currentStreamingText += t
            return
        }
        if event.type == "done" {
            commitStreamedMessage()
            return
        }
        if event.type == "error" {
            let msg = event.error ?? "Unknown error"
            if currentStreamingText.isEmpty {
                status = .error(msg)
            } else {
                commitStreamedMessage()
                status = .error(msg)
            }
        }
    }

    private func commitStreamedMessage() {
        if !currentStreamingText.isEmpty {
            let assistantMessage = ChatMessage(role: .assistant, text: currentStreamingText)
            messages.append(assistantMessage)
            try? memory.appendAndPersist(conversationId: conversationID, message: assistantMessage)
            currentStreamingText = ""
        }
        status = .done
    }
}

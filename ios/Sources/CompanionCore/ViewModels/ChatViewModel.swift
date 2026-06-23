// 聊天 ViewModel — 调 /chat/stream,累积 text delta,处理 done/error
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
    private let api: APIClientProtocol
    private var streamTask: Task<Void, Never>?

    public init(
        characterID: String,
        characterName: String,
        userID: String = AppConfig.localUserID,
        api: APIClientProtocol
    ) {
        self.characterID = characterID
        self.characterName = characterName
        self.userID = userID
        self.api = api
    }

    public func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if case .streaming = status { return }
        if case .sending = status { return }

        messages.append(ChatMessage(role: .user, text: trimmed))
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
                // 流一半出错 → 把已累积 text 当作部分回复,再报错
                commitStreamedMessage()
                status = .error(msg)
            }
        }
    }

    private func commitStreamedMessage() {
        if !currentStreamingText.isEmpty {
            messages.append(ChatMessage(role: .assistant, text: currentStreamingText))
            currentStreamingText = ""
        }
        status = .done
    }
}

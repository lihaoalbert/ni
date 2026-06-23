// IP 列表 ViewModel — 状态机: idle / loading / loaded / error
import Foundation
import Observation

@MainActor
@Observable
public final class IPListViewModel {
    public enum State: Sendable, Equatable {
        case idle
        case loading
        case loaded(items: [IPListItem])
        case error(message: String)
    }

    public private(set) var state: State = .idle
    private let api: APIClientProtocol

    public init(api: APIClientProtocol) {
        self.api = api
    }

    public func loadIfNeeded() async {
        if case .loading = state { return }
        if case .loaded = state { return }
        await load()
    }

    public func load() async {
        state = .loading
        do {
            let resp = try await api.listIPs()
            state = .loaded(items: resp.items)
        } catch let e as APIError {
            state = .error(message: e.errorDescription ?? "Unknown error")
        } catch {
            state = .error(message: error.localizedDescription)
        }
    }
}

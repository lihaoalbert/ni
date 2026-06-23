// 登录页 — Loop 6 简化:默认账号直接进,后续可加手动输入
import SwiftUI
import CompanionCore

public struct LoginView: View {
    @Bindable var appState: AppState
    @State private var email: String = "test@ni.app"
    @State private var password: String = "test1234"
    @State private var isLoading = false
    @State private var errorMessage: String?

    public init(appState: AppState) {
        self.appState = appState
    }

    public var body: some View {
        VStack(spacing: 24) {
            Spacer()
            VStack(spacing: 8) {
                Image(systemName: "person.crop.circle.badge.sparkles")
                    .font(.system(size: 64))
                    .foregroundStyle(.tint)
                Text("CompanionAI")
                    .font(.largeTitle.bold())
                Text("登录后与你的数字人对话")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(spacing: 12) {
                TextField("邮箱", text: $email)
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.emailAddress)
                    #endif
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                SecureField("密码", text: $password)
                    .textFieldStyle(.roundedBorder)
            }
            .padding(.horizontal)
            if let errorMessage {
                Text(errorMessage).foregroundStyle(.red).font(.caption)
            }
            Button(action: { Task { await doLogin() } }) {
                HStack {
                    if isLoading { ProgressView().tint(.white) }
                    Text(isLoading ? "登录中…" : "登录")
                        .bold()
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(.tint, in: RoundedRectangle(cornerRadius: 12))
                .foregroundStyle(.white)
            }
            .padding(.horizontal)
            .disabled(isLoading)
            Spacer()
        }
    }

    private func doLogin() async {
        isLoading = true
        errorMessage = nil
        do {
            try await appState.login(email: email, password: password)
        } catch {
            errorMessage = (error as? APIError)?.errorDescription ?? error.localizedDescription
        }
        isLoading = false
    }
}

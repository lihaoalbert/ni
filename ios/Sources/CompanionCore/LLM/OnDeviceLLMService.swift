/// OnDeviceLLMService — 端上 MLX LLM 封装
///
/// Loop 8 关键设计:
/// - 单例服务,App 全程一份模型;多 ViewModel 共用避免重复加载(1.7B-4bit ≈ 1.2GB RAM)
/// - 加载是异步的,首次启动会下载模型(~1.2GB,从 HuggingFace);后续启动走本地缓存
/// - 加载状态用 State 枚举暴露,UI 可观察下载/加载进度(@Observable — ChatView 直接观察 state)
/// - generate 是 async throws → String,内部用 ChatSession.respond(to:)
/// - 不在生成中 block UI,所有调用都在 background Task
///
/// 模型选择:mlx-community/Qwen3-1.7B-4bit
/// - 1.7B params @ 4-bit ≈ 1.2GB disk / RAM
/// - 中文指令跟随足够好,能输出结构化 JSON
/// - 16GB M2 + iOS Simulator 跑得动
///
/// 失败兜底:
/// - 加载失败 → state = .error,调用方决定是否走 backend fallback
/// - 生成超时 / 出错 → throws,调用方捕获并跳过本轮抽取(不影响聊天流)
import Foundation
import Observation
import MLXLLM
import MLXLMCommon

public enum OnDeviceLLMError: Error, CustomStringConvertible {
    case modelNotLoaded
    case modelLoadFailed(underlying: Error)
    case generateFailed(underlying: Error)

    public var description: String {
        switch self {
        case .modelNotLoaded:
            return "端上 LLM 尚未加载完成"
        case .modelLoadFailed(let e):
            return "模型加载失败: \(e)"
        case .generateFailed(let e):
            return "模型推理失败: \(e)"
        }
    }
}

@Observable
public final class OnDeviceLLMService: @unchecked Sendable {
    public enum State: Sendable, Equatable {
        case idle
        case downloading(progress: Double)
        case loading
        case ready
        case error(String)
    }

    /// 默认模型 — 1.7B 4-bit,中文 + 资源占用平衡
    public static let defaultModel = LLMRegistry.qwen3_1_7b_4bit

    private let modelConfiguration: ModelConfiguration
    private let modelDirectory: URL

    /// 当前状态 — @Observable 跟踪,ChatView 实时更新
    public private(set) var state: State = .idle

    private var container: ModelContainer?

    public init(
        modelConfiguration: ModelConfiguration = OnDeviceLLMService.defaultModel,
        modelDirectory: URL = OnDeviceLLMService.defaultModelDirectory()
    ) {
        self.modelConfiguration = modelConfiguration
        self.modelDirectory = modelDirectory
    }

    /// 模型默认存到 Application Support/llm-models/<model-id>/
    public static func defaultModelDirectory() -> URL {
        let fm = FileManager.default
        let base: URL
        #if os(iOS)
        if let supportDir = try? fm.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ) {
            base = supportDir
        } else {
            base = URL(fileURLWithPath: NSTemporaryDirectory())
        }
        #else
        base = URL(fileURLWithPath: NSTemporaryDirectory())
        #endif
        return base
            .appendingPathComponent("llm-models", isDirectory: true)
            .appendingPathComponent("qwen3-1.7b-4bit", isDirectory: true)
    }

    /// 加载模型 — 首次会下载 ~1.2GB,后续走本地缓存
    /// progressHandler 在 background 线程被调用,UI 要 dispatch 回主线程
    public func load(progressHandler: (@Sendable (Double) -> Void)? = nil) async throws {
        self.state = .loading

        // HF hub 默认下载目录 = ~/Library/Caches/...;但我们用 custom directory 让 iOS Simulator 也能存
        do {
            let configuration = modelConfiguration

            // 尝试加载;若本地不存在则触发下载
            let loaded = try await loadOrDownload(configuration: configuration) { progress in
                // 进度回调可能在 background 线程,主线程更新 state 让 @Observable 触发 UI
                Task { @MainActor in
                    self.state = .downloading(progress: progress.fractionCompleted)
                }
                progressHandler?(progress.fractionCompleted)
            }

            self.container = loaded
            self.state = .ready
        } catch {
            self.state = .error(error.localizedDescription)
            throw OnDeviceLLMError.modelLoadFailed(underlying: error)
        }
    }

    private func loadOrDownload(
        configuration: ModelConfiguration,
        progressHandler: @escaping @Sendable (Progress) -> Void
    ) async throws -> ModelContainer {
        // huggingFaceLoadModelContainer:检查本地缓存,缺失则从 HF 下载,带进度回调
        // MLXLLM 把模型权重存到默认 hub 目录 — 我们不强制改路径,让它走默认 cache
        // (Application Support 在 iOS Simulator 重启会被清,tmp 也会;
        // 默认 HF cache 在 Caches/,重装 App 才清 — 合理权衡)
        return try await loadModelContainer(configuration: configuration) { progress in
            progressHandler(progress)
        }
    }

    /// 单次推理 — 用于 fact extraction / summary
    /// systemPrompt 注入到 ChatSession instructions
    public func generate(prompt: String, systemPrompt: String? = nil, maxTokens: Int = 512, temperature: Float = 0.3) async throws -> String {
        guard let container else {
            throw OnDeviceLLMError.modelNotLoaded
        }
        let generateParameters = GenerateParameters(
            maxTokens: maxTokens,
            temperature: temperature
        )
        let session = ChatSession(
            container,
            instructions: systemPrompt,
            generateParameters: generateParameters
        )
        do {
            return try await session.respond(to: prompt)
        } catch {
            throw OnDeviceLLMError.generateFailed(underlying: error)
        }
    }
}

// MARK: - Protocol(测试用 — 让 MockOnDeviceLLM 能注入 FactExtractor / SummaryGenerator)

public protocol OnDeviceLLMServiceProtocol: Sendable {
    var state: OnDeviceLLMService.State { get }

    func load(progressHandler: (@Sendable (Double) -> Void)?) async throws

    func generate(
        prompt: String,
        systemPrompt: String?,
        maxTokens: Int,
        temperature: Float
    ) async throws -> String
}

extension OnDeviceLLMService: OnDeviceLLMServiceProtocol {}

// MARK: - Helper: extract progress from HuggingFace API
//
// huggingFaceLoadModelContainer 回调签名:(@Sendable (Progress) -> Void)?
// 我们直接透传,封装在 load() 里

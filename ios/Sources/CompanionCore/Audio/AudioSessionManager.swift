/// AudioSessionManager — 音频会话 ref-counted 复用
///
/// 为什么需要:
/// - 同时可能多个组件需要音频(record 监听 + TTS 播放)
/// - AVAudioSession 全局共享,谁 activate 谁 deactivate 容易错乱
/// - 切换 category 会打断当前会话 — ref-count 平滑过渡
///
/// 分类:
/// - .record       → playAndRecord / defaultToSpeaker / allowBluetooth
/// - .playback     → playback(可以后台)
/// - .playAndRecord → 同时需要录和播(本项目主路径)
///
/// 用法:
/// ```
/// AudioSessionManager.shared.enter(.playAndRecord)
/// // ... 用完
/// AudioSessionManager.shared.leave(.playAndRecord)
/// ```
/// 最后一个 `leave` 触发真正的 deactivate
///
/// iOS only:AVAudioSession 在 macOS 上不可用;macOS 用 stub 实现
import Foundation
#if canImport(AVFAudio)
import AVFAudio
#endif
#if os(iOS)
import AVFoundation
#endif

public final class AudioSessionManager: @unchecked Sendable {
    public static let shared = AudioSessionManager()

    public enum Role: Sendable, Equatable {
        case record
        case playback
        case playAndRecord
    }

    private let lock = NSLock()
    private var counts: [Role: Int] = [:]
    private var currentRole: Role?

    private init() {}

    /// 申请进入某种音频角色(ref + 1)
    /// - 第一次进入时真正激活 AVAudioSession
    /// - 同 role 重复 enter 只加 ref
    /// - 不同 role 之间切换会先 deactivate 再 activate
    public func enter(_ role: Role) {
        lock.lock()
        defer { lock.unlock() }
        counts[role, default: 0] += 1
        if currentRole == role { return }
        let previous = currentRole
        currentRole = role
        #if os(iOS)
        applyCategory(for: role)
        // 不同 role 切换 — 先 deactivate 再 activate
        if previous != nil && previous != role {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
        do {
            try AVAudioSession.sharedInstance().setActive(true, options: [])
        } catch {
            print("[AudioSessionManager] activate failed: \(error)")
        }
        #endif
    }

    /// 释放一次音频角色(ref - 1)
    /// - ref 降到 0 时 deactivate
    public func leave(_ role: Role) {
        lock.lock()
        defer { lock.unlock() }
        guard let c = counts[role], c > 0 else { return }
        counts[role] = c - 1
        let total = counts.values.reduce(0, +)
        if total == 0 {
            currentRole = nil
            #if os(iOS)
            do {
                try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            } catch {
                print("[AudioSessionManager] deactivate failed: \(error)")
            }
            #endif
        }
    }

    /// 查询某种 role 当前 ref 数
    public func refCount(for role: Role) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return counts[role, default: 0]
    }

    #if os(iOS)
    private func applyCategory(for role: Role) {
        let session = AVAudioSession.sharedInstance()
        do {
            switch role {
            case .record:
                try session.setCategory(
                    .playAndRecord,
                    mode: .default,
                    options: [.defaultToSpeaker, .allowBluetooth]
                )
            case .playback:
                try session.setCategory(
                    .playback,
                    mode: .spokenAudio,
                    options: [.duckOthers]
                )
            case .playAndRecord:
                try session.setCategory(
                    .playAndRecord,
                    mode: .default,
                    options: [.defaultToSpeaker, .allowBluetooth, .duckOthers]
                )
            }
        } catch {
            print("[AudioSessionManager] setCategory failed: \(error)")
        }
    }
    #endif
}

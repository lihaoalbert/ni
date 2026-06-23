/// DatabaseFactory — 默认数据库路径工厂
///
/// 关键决策:
/// - iOS App: Documents/companion.sqlite(用户在 iTunes 文件共享可见;iOS Data Protection 自动覆盖整目录)
/// - 测试: :memory: 或 NSTemporaryDirectory 下的随机名
/// - macOS CLI: NSTemporaryDirectory(供 swift build / swift test 跑非 UI 路径)
import Foundation

public enum DatabaseFactory {
    /// 生产路径:iOS Documents/companion.sqlite,其它平台临时目录
    public static func defaultPath(fileName: String = "companion.sqlite") -> String {
        #if os(iOS)
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
        return docs?.appendingPathComponent(fileName).path
            ?? NSTemporaryDirectory().appending(fileName)
        #else
        return NSTemporaryDirectory().appending(fileName)
        #endif
    }

    /// 临时路径(测试)
    public static func temporaryPath(fileName: String = "companion-test.sqlite") -> String {
        NSTemporaryDirectory().appending(UUID().uuidString).appending("-").appending(fileName)
    }
}

private extension String {
    func appending(_ suffix: String) -> String {
        self + suffix
    }
}

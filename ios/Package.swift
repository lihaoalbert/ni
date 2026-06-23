// swift-tools-version: 5.10
import PackageDescription

// Loop 6: iOS 17+ / Swift 6 / SwiftUI App + 库(可被未来 iOS/Android KMP 业务层复用)
// Loop 7: 加 SQLite.swift — 4 层记忆 + chat history 持久化(iOS Data Protection .completeUntilFirstUserAuthentication)
let package = Package(
    name: "CompanionAI",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),  // 仅用于 swift build / swift test 在 macOS 上验证非 UI 逻辑
    ],
    products: [
        .library(name: "CompanionCore", targets: ["CompanionCore"]),
        .executable(name: "CompanionAI", targets: ["CompanionAI"]),
    ],
    dependencies: [
        // Loop 7: SQLite 持久层 — Stephen Celis 的 SQLite.swift(stable, 纯 Swift, Expression API)
        // 0.15.3 兼容 swift-tools-version 5.9(我们用 5.10,匹配);master / 0.16.0 已升到 6.1,太新
        .package(url: "https://github.com/stephencelis/SQLite.swift.git", from: "0.15.3"),
    ],
    targets: [
        // 库:纯 Foundation + 模型 + 网络层 + 存储 + 记忆(SwiftUI 无关,Android KMP 复用候选)
        .target(
            name: "CompanionCore",
            dependencies: [
                .product(name: "SQLite", package: "SQLite.swift"),
            ],
            path: "Sources/CompanionCore"
        ),
        // App:SwiftUI Views + 路由 + ViewModels
        .executableTarget(
            name: "CompanionAI",
            dependencies: ["CompanionCore"],
            path: "Sources/CompanionAI"
        ),
        // 测试:CompanionCore 逻辑(SSEReader 解析、APIClient 序列化)
        .testTarget(
            name: "CompanionCoreTests",
            dependencies: ["CompanionCore"],
            path: "Tests/CompanionCoreTests"
        ),
    ]
)

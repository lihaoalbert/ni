// swift-tools-version: 5.10
import PackageDescription

// Loop 6: iOS 17+ / Swift 6 / SwiftUI App + 库(可被未来 iOS/Android KMP 业务层复用)
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
    targets: [
        // 库:纯 Foundation + 模型 + 网络层(SwiftUI 无关,Android KMP 复用候选)
        .target(
            name: "CompanionCore",
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

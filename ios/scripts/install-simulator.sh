#!/bin/bash
# install-simulator.sh — 构建 + 安装 iOS App 到模拟器
#
# 用法:
#   ./scripts/install-simulator.sh                       # 默认 iPhone 16
#   ./scripts/install-simulator.sh "iPhone 16 Pro"       # 指定设备
#
# 解决了 SwiftPM xcodebuild 不自动生成 .app bundle 的问题:
# 1. 跑 xcodebuild 出二进制
# 2. 手动建 .app 结构
# 3. 把二进制 + Info.plist + 资源 bundle(MLX / Hub 的 .metallib)塞进去
# 4. 用 simctl install 部署
#
# Loop 8 关键:把 mlx-swift_Cmlx.bundle 一起拷进去,iOS 模拟器里 MLX 才能找到 default.metallib
set -euo pipefail

DEVICE_NAME="${1:-iPhone 16}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
BUILD_DIR="$ROOT_DIR/.build/Build/Products/Debug-iphonesimulator"
APP_NAME="CompanionAI"
BUNDLE_ID="com.niapp.${APP_NAME}"

echo "==> build"
(cd "$ROOT_DIR" && xcodebuild \
    -scheme "$APP_NAME" \
    -destination "platform=iOS Simulator,name=${DEVICE_NAME}" \
    -configuration Debug \
    -derivedDataPath "$ROOT_DIR/.build" \
    -clonedSourcePackagesDirPath "$ROOT_DIR/.build/SourcePackages" \
    -skipPackagePluginValidation \
    -onlyUsePackageVersionsFromResolvedFile \
    -quiet)

echo "==> find simulator"
DEVICE_UDID=$(xcrun simctl list devices "iOS" | grep "${DEVICE_NAME} (" | head -1 | grep -oE "[0-9A-F-]{36}")
if [ -z "$DEVICE_UDID" ]; then
    echo "error: no simulator named ${DEVICE_NAME}"
    exit 1
fi
echo "  device: $DEVICE_UDID"

echo "==> prepare .app bundle"
APP_CONTAINER=$(xcrun simctl get_app_container "$DEVICE_UDID" "$BUNDLE_ID" 2>/dev/null || true)
if [ -n "$APP_CONTAINER" ]; then
    # 已有 bundle 目录,直接覆盖二进制和资源
    cp -f "$BUILD_DIR/$APP_NAME" "$APP_CONTAINER/$APP_NAME"
    echo "  binary updated"
    # Loop 10 修 crash:SwiftPM 不让 Info.plist 作为 resource(target build 自动生成的 plist
    # 缺 NSMicrophone/SpeechRecognition UsageDescription → 进 ChatView 调
    # SFSpeechRecognizer 时 TCC abort).从源拷贝覆盖 bundle 的 plist。
    if [ -f "$ROOT_DIR/Sources/CompanionAI/Resources/Info.plist" ]; then
        cp -f "$ROOT_DIR/Sources/CompanionAI/Resources/Info.plist" "$APP_CONTAINER/Info.plist"
        echo "  Info.plist replaced (with UsageDescription keys)"
    fi
else
    # 没有 — 错误,先手动 install 一次
    echo "error: $BUNDLE_ID not installed yet. Run xcrun simctl install once with the .app, or use Xcode."
    exit 1
fi

# Loop 8: 把 MLX / Hub 的资源 bundle(包含 default.metallib)放进 app
for resource_bundle in mlx-swift_Cmlx.bundle swift-transformers_Hub.bundle; do
    if [ -d "$BUILD_DIR/$resource_bundle" ]; then
        cp -Rf "$BUILD_DIR/$resource_bundle" "$APP_CONTAINER/"
        echo "  $resource_bundle deployed"
    fi
done

# 顶层如果有多余的 default.metallib(早期调试放的),清掉 — 让 Cmlx bundle 的版本生效
[ -f "$APP_CONTAINER/default.metallib" ] && rm "$APP_CONTAINER/default.metallib" && echo "  removed stray default.metallib"

echo "==> re-sign"
codesign -f -s - "$APP_CONTAINER"

echo "==> launch"
xcrun simctl launch "$DEVICE_UDID" "$BUNDLE_ID"
echo "==> done"
echo "    watch logs:  xcrun simctl spawn $DEVICE_UDID log stream --predicate 'processImagePath contains \"$APP_NAME\"'"
echo "    stop:         xcrun simctl terminate $DEVICE_UDID $BUNDLE_ID"

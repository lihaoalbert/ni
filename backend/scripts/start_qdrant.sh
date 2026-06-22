#!/usr/bin/env bash
# 启动本地 Qdrant — Phase 2 向量库
# 用法: ./scripts/start_qdrant.sh [start|stop|status]
set -euo pipefail

QDRANT_DIR="$(cd "$(dirname "$0")/.." && pwd)/.qdrant"
QDRANT_BIN="$QDRANT_DIR/qdrant"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
PID_FILE="$QDRANT_DIR/qdrant.pid"
LOG_FILE="$QDRANT_DIR/qdrant.log"

cmd="${1:-start}"

# 检查 binary
if [[ ! -x "$QDRANT_BIN" ]]; then
    echo "✗ qdrant binary 不存在: $QDRANT_BIN"
    echo "  安装方法: curl -L https://gh-proxy.com/https://github.com/qdrant/qdrant/releases/latest/download/qdrant-aarch64-apple-darwin.tar.gz | tar -xz -C $QDRANT_DIR --strip-components=1"
    exit 1
fi

case "$cmd" in
    start)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✓ qdrant 已在运行 (pid=$(cat "$PID_FILE"))"
            exit 0
        fi
        echo "▶ 启动 qdrant ..."
        nohup "$QDRANT_BIN" --uri "$QDRANT_URL" > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        sleep 2
        if curl -sf -m 2 "$QDRANT_URL/healthz" >/dev/null; then
            echo "✓ qdrant 已启动 (pid=$(cat "$PID_FILE") url=$QDRANT_URL)"
        else
            echo "✗ qdrant 启动失败,看日志: $LOG_FILE"
            exit 1
        fi
        ;;
    stop)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            kill "$(cat "$PID_FILE")"
            rm -f "$PID_FILE"
            echo "✓ qdrant 已停止"
        else
            echo "○ qdrant 没在运行"
            rm -f "$PID_FILE"
        fi
        ;;
    status)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "✓ qdrant 正在运行 (pid=$(cat "$PID_FILE"))"
            curl -s "$QDRANT_URL/healthz"
        else
            echo "✗ qdrant 未运行"
            exit 1
        fi
        ;;
    *)
        echo "用法: $0 [start|stop|status]"
        exit 1
        ;;
esac
#!/bin/bash
# start-backend.sh — 一键起后端 + ips-mock(Loop 10.3)
#
# 同时启动两个服务:
#   - chat backend (FastAPI) — :8000,iOS 走这个
#   - ips-mock platform      — :8001,iOS 走这个拉 IP 列表
#
# 用法:
#   ./scripts/start-backend.sh           # 前台跑,tail 日志 + 转发 Ctrl+C
#   ./scripts/start-backend.sh --bg      # 后台跑,只打启动信息 + 退出
#   ./scripts/start-backend.sh --stop    # 停掉之前后台起的进程
#   ./scripts/start-backend.sh --status  # 看当前在跑没
#
# 端口:8000 = chat backend(必须)
#       8001 = ips-mock platform(必须)
#       8000 / 8001 占用时会报错退出,不会强 kill
set -uo pipefail

ROOT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
BACKEND_DIR="$ROOT_DIR/backend"
IPS_MOCK_DIR="$ROOT_DIR/ips-mock"
LOG_DIR="$ROOT_DIR/.run-logs"
PID_DIR="$ROOT_DIR/.run-pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

CHAT_PORT="${CHAT_PORT:-8000}"
PLATFORM_PORT="${PLATFORM_PORT:-8001}"
BACKEND_LOG="$LOG_DIR/backend.log"
IPS_MOCK_LOG="$LOG_DIR/ips-mock.log"
BACKEND_PID="$PID_DIR/backend.pid"
IPS_MOCK_PID="$PID_DIR/ips-mock.pid"

# 颜色(只在 TTY 下用)
if [ -t 1 ]; then
    C_BOLD="\033[1m"; C_DIM="\033[2m"; C_RED="\033[31m"
    C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RESET="\033[0m"
else
    C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_RESET=""
fi

# ===== helpers =====

log() { echo -e "${C_DIM}[$(date +%H:%M:%S)]${C_RESET} $*"; }

err() { echo -e "${C_RED}error:${C_RESET} $*" >&2; }

port_in_use() {
    lsof -ti:"$1" 2>/dev/null | head -1
}

ensure_dirs() {
    [ -d "$BACKEND_DIR" ] || { err "找不到 $BACKEND_DIR"; exit 1; }
    [ -d "$IPS_MOCK_DIR" ] || { err "找不到 $IPS_MOCK_DIR"; exit 1; }
}

# 用 uv run 启 uvicorn,带上 reload + 颜色
start_uvicorn() {
    local dir="$1" port="$2" logfile="$3" pidfile="$4" name="$5"
    log "${C_BOLD}$name${C_RESET}  → :$port  (cwd=$dir)"
    (cd "$dir" && uv run uvicorn app.main:app --port "$port" --reload \
        > "$logfile" 2>&1 & echo $! > "$pidfile")
    local pid
    pid=$(cat "$pidfile")
    log "  pid=$pid  log=$logfile"
}

# ===== actions =====

action_start_fg() {
    ensure_dirs
    # 端口占用检测
    for port in "$CHAT_PORT" "$PLATFORM_PORT"; do
        if pid=$(port_in_use "$port"); then
            err "端口 $port 已被 pid=$pid 占用,先 stop 或换端口(CHAT_PORT / PLATFORM_PORT)"
            exit 1
        fi
    done
    start_uvicorn "$BACKEND_DIR"  "$CHAT_PORT"     "$BACKEND_LOG"  "$BACKEND_PID"  "chat-backend"
    start_uvicorn "$IPS_MOCK_DIR" "$PLATFORM_PORT" "$IPS_MOCK_LOG" "$IPS_MOCK_PID" "ips-mock"

    log ""
    log "${C_GREEN}两个服务都起来了${C_RESET},按 ${C_BOLD}Ctrl+C${C_RESET} 停"
    log "  chat backend   http://localhost:$CHAT_PORT     (TTS /voice/tts/info)"
    log "  ips-mock       http://localhost:$PLATFORM_PORT (登录 / IP 列表)"
    log ""

    cleanup() {
        log ""
        log "收到信号,清理…"
        for pf in "$BACKEND_PID" "$IPS_MOCK_PID"; do
            if [ -f "$pf" ]; then
                local p
                p=$(cat "$pf")
                if kill -0 "$p" 2>/dev/null; then
                    kill "$p" 2>/dev/null && log "  stopped pid=$p"
                fi
                rm -f "$pf"
            fi
        done
        # 兜底:按端口再杀一次
        for port in "$CHAT_PORT" "$PLATFORM_PORT"; do
            if pid=$(port_in_use "$port"); then
                kill "$pid" 2>/dev/null && log "  killed port=$port pid=$pid"
            fi
        done
        log "done"
    }
    trap cleanup INT TERM EXIT

    # tail 两个日志直到被中断
    tail -n +1 -F "$BACKEND_LOG" "$IPS_MOCK_LOG" 2>/dev/null &
    wait
}

action_start_bg() {
    ensure_dirs
    for port in "$CHAT_PORT" "$PLATFORM_PORT"; do
        if pid=$(port_in_use "$port"); then
            err "端口 $port 已被 pid=$pid 占用,先 stop 或换端口"
            exit 1
        fi
    done
    # 后台跑:noHUP + subshell + disown,脱离当前 tty,Ctrl+C 不影响
    # macOS 没 setsid,用 (subshell) + nohup + & + disown 达成同样效果
    (cd "$BACKEND_DIR" && nohup uv run uvicorn app.main:app --port "$CHAT_PORT" --reload \
        > "$BACKEND_LOG" 2>&1 & echo $! > "$BACKEND_PID"; disown) </dev/null
    (cd "$IPS_MOCK_DIR" && nohup uv run uvicorn app.main:app --port "$PLATFORM_PORT" --reload \
        > "$IPS_MOCK_LOG" 2>&1 & echo $! > "$IPS_MOCK_PID"; disown) </dev/null

    sleep 3
    log "${C_GREEN}后台启动了${C_RESET}"
    log "  chat backend   http://localhost:$CHAT_PORT     pid=$(cat "$BACKEND_PID")  log=$BACKEND_LOG"
    log "  ips-mock       http://localhost:$PLATFORM_PORT pid=$(cat "$IPS_MOCK_PID")  log=$IPS_MOCK_LOG"
    log ""
    log "看日志:  tail -f $BACKEND_LOG $IPS_MOCK_LOG"
    log "停掉:    $0 --stop"
}

action_stop() {
    for pf in "$BACKEND_PID" "$IPS_MOCK_PID"; do
        if [ -f "$pf" ]; then
            local p
            p=$(cat "$pf")
            if kill -0 "$p" 2>/dev/null; then
                kill "$p" && log "stopped pid=$p (from $pf)"
            else
                log "stale pid file $pf, removing"
            fi
            rm -f "$pf"
        fi
    done
    # 兜底按端口
    for port in "$CHAT_PORT" "$PLATFORM_PORT"; do
        if pid=$(port_in_use "$port"); then
            log "port $port 仍有 pid=$pid,kill 掉"
            kill "$pid"
        fi
    done
}

action_status() {
    for label in "chat-backend:$CHAT_PORT:$BACKEND_PID" \
                 "ips-mock:$PLATFORM_PORT:$IPS_MOCK_PID"; do
        IFS=":" read -r name port pidf <<< "$label"
        if pid=$(port_in_use "$port"); then
            echo -e "${C_GREEN}●${C_RESET} $name  :$port  pid=$pid  ${C_DIM}(listening)${C_RESET}"
        else
            echo -e "${C_DIM}○${C_RESET} $name  :$port  ${C_DIM}(not running)${C_RESET}"
        fi
    done
}

# ===== main =====

case "${1:-}" in
    --bg)     action_start_bg ;;
    --stop)   action_stop ;;
    --status) action_status ;;
    "")       action_start_fg ;;
    -h|--help)
        sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *)
        err "未知参数: $1"
        echo "用法: $0 [--bg | --stop | --status]"
        exit 1
        ;;
esac

#!/bin/bash
# server-setup.sh — 在干净的阿里云 ECS 上初始化 ni 项目运行环境
# 适用:Ubuntu 22.04+ / Debian 12+
# 用法:./deploy/server-setup.sh
#
# 不做的事:
# - 不动 nginx(假设你已有 ibiren 项目的 nginx 配置)
# - 不动 Postgres / MySQL / Redis(假设你已有 RDS / 自托管实例)
# - 不申请 TLS 证书(由 certbot 单独处理,见 certbot-issue.sh)
# - 不创建数据库(SQLite 文件由 backend 进程自动建)

set -euo pipefail

# ===== 配置区(部署前改这里)=====
DEPLOY_USER="${DEPLOY_USER:-ni}"
APP_DIR="${APP_DIR:-/opt/ni}"
DOMAIN="${DOMAIN:-ni.idata.mobi}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
IPS_MOCK_PORT="${IPS_MOCK_PORT:-8001}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

# 日志
log() { echo -e "\033[2m[$(date +%H:%M:%S)]\033[0m $*"; }
err() { echo -e "\033[31merror:\033[0m $*" >&2; }

# ===== 前置检查 =====
if [[ $EUID -ne 0 ]]; then
    err "需要 root 权限(sudo $0)"
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    err "只支持 apt 系发行版(Ubuntu/Debian)"
    exit 1
fi

# ===== Step 1:系统包 =====
log "Step 1/6: 安装系统包(ffmpeg, build-essential, python, nginx, certbot)"
apt-get update -y
apt-get install -y \
    software-properties-common \
    build-essential \
    git curl wget \
    ffmpeg \
    python3 python3-venv python3-dev \
    nginx certbot python3-certbot-nginx

# uv(Python 包管理器,比 pip 快 10x)
if ! command -v uv >/dev/null 2>&1; then
    log "  安装 uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv 装到 /root/.local/bin,加到 PATH
    export PATH="$HOME/.local/bin:$PATH"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> /root/.bashrc
fi

# ===== Step 2:创建部署用户 =====
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    log "Step 2/6: 创建部署用户 $DEPLOY_USER"
    useradd -m -s /bin/bash "$DEPLOY_USER"
    # 让 deploy 用户能写 /opt/ni
    mkdir -p "$APP_DIR"
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
else
    log "Step 2/6: 部署用户 $DEPLOY_USER 已存在"
fi

# ===== Step 3:clone 代码 =====
if [[ ! -d "$APP_DIR/.git" ]]; then
    log "Step 3/6: clone 代码到 $APP_DIR"
    sudo -u "$DEPLOY_USER" git clone https://github.com/lihaoalbert/ni.git "$APP_DIR"
else
    log "Step 3/6: 代码已存在,跳过 clone"
    cd "$APP_DIR" && sudo -u "$DEPLOY_USER" git pull --rebase --autostash
fi

# ===== Step 4:装 Python 依赖 =====
log "Step 4/6: 装 backend / ips-mock Python 依赖"
cd "$APP_DIR/backend"
sudo -u "$DEPLOY_USER" uv sync --all-extras --no-dev

cd "$APP_DIR/ips-mock"
sudo -u "$DEPLOY_USER" uv sync --all-extras --no-dev

# ===== Step 5:配置 .env(无密钥,只占位)=====
log "Step 5/6: 写 .env(密钥由你手动填)"
if [[ ! -f "$APP_DIR/backend/.env" ]]; then
    cat > "$APP_DIR/backend/.env" <<EOF
# 生产环境配置(密钥由 deploy owner 手动填)
APP_ENV=production
APP_HOST=127.0.0.1
APP_PORT=$BACKEND_PORT
LOG_LEVEL=INFO
LOG_JSON=1

# ===== LLM =====
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=__FILL_ME__
CLAUDE_MODEL_MAIN=claude-sonnet-4-6

# ===== Memory(facts 记忆)=====
# inmemory: 进程内 dict,无外部依赖,重启即丢(默认,够用)
# qdrant:   向量库,需先装 qdrant 二进制(ni-qdrant.service)
# 聊天历史 / 消息 由 iOS 端本地 SQLite 存,backend 不持久化。
MEMORY_BACKEND=inmemory

# ===== Qdrant(本地 binary)=====
QDRANT_URL=http://127.0.0.1:6333

# ===== Redis(已有实例)=====
REDIS_URL=redis://127.0.0.1:6379/0

# ===== TTS / STT =====
TTS_PROVIDER=volcengine
STT_PROVIDER=mock
VOLC_API_KEY=__FILL_ME__
VOLC_RESOURCE_ID=seed-tts-2.0
VOLC_APP_ID=__FILL_ME__
VOLC_ACCESS_KEY=__FILL_ME__
VOLC_SECRET_KEY=__FILL_ME__
VOLC_DEFAULT_VOICE=saturn_zh_female_cancan_tob
TTS_CACHE_BACKEND=redis
TTS_CACHE_TTL_SECONDS=604800
EOF
    chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/backend/.env"
    chmod 600 "$APP_DIR/backend/.env"
    log "  ⚠️  $APP_DIR/backend/.env 写好了,__FILL_ME__ 字段需要你手动填"
fi

mkdir -p "$APP_DIR/backend/data"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/backend/data"
mkdir -p "$APP_DIR/backend/data/qdrant"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/backend/data/qdrant"

# ===== Step 6:装 systemd units =====
log "Step 6/6: 装 systemd units"
cp "$APP_DIR/deploy/systemd/ni-backend.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/ni-ips-mock.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/ni-qdrant.service" /etc/systemd/system/ 2>/dev/null || true

systemctl daemon-reload
systemctl enable ni-backend ni-ips-mock
systemctl restart ni-backend ni-ips-mock

# ===== 完成 =====
log ""
log "✅ 部署完成"
log ""
log "下一步:"
log "  1. 编辑 $APP_DIR/backend/.env,把 4 个 __FILL_ME__ 填上"
log "  2. sudo systemctl restart ni-backend"
log "  3. 检查状态:systemctl status ni-backend ni-ips-mock"
log "  4. 部署 nginx 配置:cp $APP_DIR/deploy/nginx/ni.conf /etc/nginx/sites-available/ && ln -s /etc/nginx/sites-available/ni.conf /etc/nginx/sites-enabled/"
log "  5. nginx -t && systemctl reload nginx"
log "  6. 申请 TLS:certbot --nginx -d $DOMAIN"

#!/bin/bash
# aliyun-setup.sh — Alibaba Cloud Linux 4 专用部署脚本
# 适用:OpenAnolis 8 / alinux4 / CentOS 8 / RHEL 8 / Fedora
# 用法:sudo bash deploy/aliyun-setup.sh
#
# 跟 server-setup.sh 的区别:
#   - 用 dnf 不是 apt
#   - nginx 走 /etc/nginx/conf.d/ (RedHat 风格,不是 sites-enabled/)
#   - 跳过 certbot — 用 ibi.idata.mobi 已有的 wildcard SSL 证书
#   - 不创建 ni 用户(ibiren 也是 root 跑,保持一致)
#
# 不做的事:
#   - 不动 nginx.conf / conf.d/ibiren.conf(已部署)
#   - 不动 Redis / MySQL RDS(ibiren 在用)
#   - 不申请新 TLS 证书

set -euo pipefail

# ===== 配置 =====
APP_DIR="${APP_DIR:-/opt/ni}"
DOMAIN="${DOMAIN:-ni.idata.mobi}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
IPS_MOCK_PORT="${IPS_MOCK_PORT:-8001}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
BACKEND_REPO="https://github.com/lihaoalbert/ni.git"

# 日志
log() { echo -e "\033[2m[$(date +%H:%M:%S)]\033[0m $*"; }
err() { echo -e "\033[31merror:\033[0m $*" >&2; }

# ===== 前置检查 =====
if [[ $EUID -ne 0 ]]; then
    err "需要 root 权限(sudo $0)"
    exit 1
fi

if ! command -v dnf >/dev/null 2>&1; then
    err "只支持 dnf 系发行版(aliyun4/centos8/rhel8/fedora)"
    exit 1
fi

# ===== Step 1:系统包 =====
log "Step 1/7: dnf install python3.11 / git / curl / wget / nginx"
# alinux4 默认源已有 python3.11
dnf install -y \
    git curl wget \
    gcc gcc-c++ make \
    openssl-devel libffi-devel \
    python3.11 python3.11-devel python3.11-pip \
    nginx

# 验证
python3.11 --version
nginx -v 2>&1

# ffmpeg:alinux4 默认源无包;RPM Fusion 配置麻烦且 V3 直出 ogg_opus 不需要
# 见 backend/app/voice/volcengine.py:is_ffmpeg_available() — 优雅降级
# 真要装:yum install --enablerepo=rpmfusion-free ffmpeg (需要先配 rpmfusion repo)

# uv(Python 包管理器,比 pip 快 10x)
if ! command -v uv >/dev/null 2>&1; then
    log "  安装 uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> /root/.bashrc
fi
uv --version

# 配置清华 PyPI 镜像(国内 ECS 默认 PyPI 慢到无法用)
# 也写到 /etc/profile.d 让所有用户都能用
mkdir -p /root/.config/uv
cat > /root/.config/uv/uv.toml <<'EOF'
# 清华 PyPI 镜像(中国 ECS 必备)
index-url = "https://pypi.tuna.tsinghua.edu.cn/simple"

[pip]
index-url = "https://pypi.tuna.tsinghua.edu.cn/simple"
EOF
cat > /etc/profile.d/uv-tuna.sh <<'EOF'
export UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
EOF
chmod +x /etc/profile.d/uv-tuna.sh
log "  uv 配置: 用清华 PyPI 镜像"

# ===== Step 2:建 /opt/ni + 拉代码 =====
log "Step 2/7: clone 代码到 $APP_DIR"
if [[ ! -d "$APP_DIR/.git" ]]; then
    mkdir -p "$APP_DIR"
    git clone "$BACKEND_REPO" "$APP_DIR"
else
    log "  代码已存在,跳过 clone"
    cd "$APP_DIR" && git pull --rebase --autostash
fi

# ===== Step 3:uv sync =====
log "Step 3/7: uv sync (backend + ips-mock)"
cd "$APP_DIR/backend"
uv sync --all-extras --no-dev

cd "$APP_DIR/ips-mock"
uv sync --all-extras --no-dev

# ===== Step 4:prod .env(无密钥占位,deploy 后手填)=====
log "Step 4/7: 写 .env 占位(密钥手填)"
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
CLAUDE_MODEL_LIGHT=claude-haiku-4-5-20251001

# ===== Memory =====
# inmemory: 进程内 dict,无外部依赖(默认,够用)
# qdrant:   需装 qdrant 二进制 + 改 ni-qdrant.service
MEMORY_BACKEND=inmemory

# ===== Redis(共享 ibiren 的,db=0)=====
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
TTS_CACHE_BACKEND=memory
TTS_CACHE_TTL_SECONDS=604800
EOF
    chmod 600 "$APP_DIR/backend/.env"
    log "  ⚠️  $APP_DIR/backend/.env 写好了,__FILL_ME__ 需要手填"
fi

mkdir -p "$APP_DIR/backend/data"
mkdir -p "$APP_DIR/backend/data/qdrant"
mkdir -p "$APP_DIR/ips-mock/data"

# ===== Step 5:systemd units =====
log "Step 5/7: 装 systemd units"
cp "$APP_DIR/deploy/systemd/ni-backend.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/ni-ips-mock.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/ni-qdrant.service" /etc/systemd/system/

systemctl daemon-reload
# 不在这里 start — 等密钥填了再启
systemctl enable ni-backend ni-ips-mock

# ===== Step 6:nginx ni.conf =====
log "Step 6/7: 装 nginx ni.conf (RedHat 风格 → /etc/nginx/conf.d/)"
cp "$APP_DIR/deploy/nginx/ni.conf" /etc/nginx/conf.d/ni.conf

# nginx -t 测语法
nginx -t
log "  nginx -t OK"

# ===== Step 7:启动 + 验证 =====
log "Step 7/7: 启动 ni-backend / ni-ips-mock"
systemctl restart ni-backend ni-ips-mock

# 给点时间让进程起来
sleep 3
systemctl is-active ni-backend ni-ips-mock

# ===== 完成 =====
log ""
log "✅ 部署骨架完成"
log ""
log "下一步:"
log "  1. 编辑 $APP_DIR/backend/.env,把 __FILL_ME__ 填上(密钥)"
log "  2. sudo systemctl restart ni-backend ni-ips-mock"
log "  3. sudo journalctl -u ni-backend -n 50 --no-pager  (看启动日志)"
log "  4. curl http://127.0.0.1:$BACKEND_PORT/health  (本机)"
log "  5. sudo nginx -t && sudo systemctl reload nginx  (让 ni.conf 生效)"
log "  6. curl https://$DOMAIN/health  (走 wildcard SSL)"
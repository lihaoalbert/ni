#!/bin/bash
# certbot-issue.sh — 为 ni.idata.mobi 申请 Let's Encrypt TLS 证书
# 用法:./deploy/certbot-issue.sh
#
# 前置:
#   1. deploy/server-setup.sh 跑完
#   2. deploy/nginx/ni.conf 已 cp 到 /etc/nginx/sites-available/,且 ln -s 到 sites-enabled/
#   3. nginx -t 通过,systemctl reload nginx 成功
#   4. 域名 ni.idata.mobi 已解析到本机公网 IP(8.133.241.103)

set -euo pipefail

DOMAIN="${DOMAIN:-ni.idata.mobi}"
EMAIL="${EMAIL:-admin@idata.mobi}"  # 换你的真实邮箱

# ===== 前置检查 =====
if [[ $EUID -ne 0 ]]; then
    echo "需要 root 权限(sudo $0)" >&2
    exit 1
fi

if ! command -v certbot >/dev/null 2>&1; then
    echo "certbot 没装,先跑 deploy/server-setup.sh" >&2
    exit 1
fi

if [[ ! -f /etc/nginx/sites-enabled/ni.conf ]]; then
    echo "/etc/nginx/sites-enabled/ni.conf 不存在,先把 nginx 配置装上" >&2
    exit 1
fi

# 检查 DNS 是否已解析
RESOLVED_IP=$(dig +short "$DOMAIN" | head -1 || true)
if [[ -z "$RESOLVED_IP" ]]; then
    echo "DNS 还没解析到 $DOMAIN,先去域名控制台加 A 记录" >&2
    exit 1
fi
echo "DNS 解析:$DOMAIN → $RESOLVED_IP"

# ===== 干跑一遍(避免真的发出 rate-limit 请求)=====
echo ""
echo "=== Step 1/2: certbot --nginx dry-run ==="
certbot certonly --nginx \
    --domain "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --dry-run

# ===== 真申请 =====
echo ""
echo "=== Step 2/2: certbot --nginx 真申请 ==="
certbot --nginx \
    --domain "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --redirect   # HTTP 自动 301 → HTTPS

# ===== 验证自动续期 =====
echo ""
echo "=== 验证自动续期(应该看到 Congratulations) ==="
certbot renew --dry-run

# ===== 完成 =====
echo ""
echo "✅ 证书申请完成"
echo ""
echo "证书位置:"
echo "  /etc/letsencrypt/live/$DOMAIN/fullchain.pem"
echo "  /etc/letsencrypt/live/$DOMAIN/privkey.pem"
echo ""
echo "下一步:"
echo "  1. curl -I https://$DOMAIN/health  (应该 200)"
echo "  2. ios App 里把 BackendURL 改成 https://$DOMAIN"

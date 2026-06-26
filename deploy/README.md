# ni 部署文档

阿里云 ECS(8.133.241.103)上把 ni 项目跑起来的全过程。**不使用 Docker**,用 systemd 直跑 + nginx 反代 + Let's Encrypt TLS。

## 整体架构

```
┌──────────────────────────────────────────────────────────┐
│ 公网 ni.idata.mobi :443                                  │
└────────────────────────┬─────────────────────────────────┘
                         │ nginx (server_name 路由)
                         ▼
┌──────────────────────────────────────────────────────────┐
│ 127.0.0.1:8000   ni-backend.service                      │
│   - FastAPI / uvicorn (2 workers)                        │
│   - Claude API 代理 + LLM 流式 + 火山 TTS                │
│   - SQLite (data/companion.db)                           │
│   - Redis (共享,db=0)                                    │
│   - Qdrant (可选,本地 binary,6333)                       │
└──────────────────────────────────────────────────────────┘
                         │
                         │ 内部转发 /v1/* → ips-mock
                         ▼
┌──────────────────────────────────────────────────────────┐
│ 127.0.0.1:8001   ni-ips-mock.service                     │
│   - 平台 API Mock (FastAPI)                              │
│   - /v1/characters /v1/auth/token /v1/cdn/*             │
│   - JSON 文件持久化 (data/characters.json)               │
└──────────────────────────────────────────────────────────┘
```

## 文件清单

```
deploy/
├── server-setup.sh           # 在新 ECS 上一次性初始化
├── certbot-issue.sh          # 申请 Let's Encrypt TLS
├── systemd/
│   ├── ni-backend.service    # FastAPI :8000
│   ├── ni-ips-mock.service   # FastAPI :8001
│   └── ni-qdrant.service     # 可选,本地向量库
└── nginx/
    └── ni.conf               # ni.idata.mobi server block
```

## 一次性部署(从零)

```bash
# 1. ssh 到 ECS
ssh root@8.133.241.103

# 2. 拉代码(或 git clone,或 scp 上传)
cd /opt
git clone https://github.com/lihaoalbert/ni.git  # 或你已有的

# 3. 跑 server-setup.sh(约 5-10 分钟,装系统包 + uv sync)
cd /opt/ni
bash deploy/server-setup.sh

# 4. 填密钥
vim /opt/ni/backend/.env
# 替换 4 个 __FILL_ME__:
#   ANTHROPIC_API_KEY
#   VOLC_API_KEY
#   VOLC_APP_ID
#   VOLC_ACCESS_KEY / VOLC_SECRET_KEY(可选,V3 大模型只需 X-Api-Key)

# 5. 重启 backend 让 .env 生效
sudo systemctl restart ni-backend ni-ips-mock

# 6. 装 nginx 配置
sudo cp /opt/ni/deploy/nginx/ni.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/ni.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 7. 申请 TLS
sudo bash /opt/ni/deploy/certbot-issue.sh

# 8. 验证
curl -I https://ni.idata.mobi/health
```

## 日常运维

### 查看状态

```bash
sudo systemctl status ni-backend ni-ips-mock
```

### 看日志

```bash
# 实时跟随
sudo journalctl -u ni-backend -f

# 最近 100 行
sudo journalctl -u ni-backend -n 100 --no-pager

# 报错过滤
sudo journalctl -u ni-backend -p err --since "1 hour ago"
```

### 更新代码

```bash
cd /opt/ni
sudo -u ni git pull --rebase --autostash
cd backend && sudo -u ni uv sync --all-extras --no-dev
cd ../ips-mock && sudo -u ni uv sync --all-extras --no-dev
sudo systemctl restart ni-backend ni-ips-mock
```

### 回滚

```bash
cd /opt/ni
sudo -u ni git log --oneline -5              # 找上一个稳定版本
sudo -u ni git reset --hard <commit-hash>
sudo systemctl restart ni-backend ni-ips-mock
```

## 端口规划

| 端口 | 服务 | 暴露范围 |
|---|---|---|
| 80   | nginx (HTTP → HTTPS 重定向) | 公网 |
| 443  | nginx (TLS 终止) | 公网 |
| 127.0.0.1:8000 | ni-backend (FastAPI) | 仅本机 |
| 127.0.0.1:8001 | ni-ips-mock (FastAPI) | 仅本机 |
| 127.0.0.1:6333 | Qdrant (可选) | 仅本机 |

**跟 ibiren 共用 nginx** — 通过 `server_name` 区分(`ibi.idata.mobi` 和 `ni.idata.mobi`),不冲突。

## 数据存储

- **聊天历史 / 消息 / 事实**:iOS 端本地 SQLite(`companion.db`)
  - backend 不持久化聊天,重启即清空
  - 跨设备同步未来做(Loop 13+)
- **Memory(facts 语义记忆)**:`inmemory`(默认)或 `qdrant`
  - `inmemory`:进程内 dict,无外部依赖,重启即丢,够 demo
  - `qdrant`:跑 `ni-qdrant.service`,数据落 `/opt/ni/backend/data/qdrant/`
- **TTS 缓存**:Redis db=0(共享现有 Redis)
  - key:`tts:cache:v1:<sha256(text+voice)>` → mp3 bytes
  - TTL:7 天(`TTS_CACHE_TTL_SECONDS=604800`)
- **IPS Mock 数据**:JSON 文件 `/opt/ni/ips-mock/data/characters.json`
  - Mock 服务从这读角色元数据

## 跟 ibiren 共存的关键点

1. **nginx 已占 80/443** — 我们只新增 server block,不动 default server。
2. **共享 Redis** — 用 db=0(同一个 keyspace),key 前缀 `tts:cache:v1:`,不会撞。
3. **不申请独立 RDS** — 用 SQLite,文件落在 `/opt/ni/backend/data/`,权限 700 给 ni 用户。
4. **systemd unit 命名带 `ni-` 前缀** — 跟 ibiren 的 `ibi-*` 区分。

## 安全清单

- [x] systemd unit 加了 `NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome=true`
- [x] `.env` 权限 600,只 `ni` 用户可读
- [x] backend / ips-mock 只绑 127.0.0.1,公网只能通过 nginx 进
- [x] nginx 转发头带 `X-Forwarded-Proto`,backend 知道是 HTTPS
- [x] 流式响应 `proxy_buffering off`,LLM 流式不会卡
- [x] 资源限制 `MemoryMax=1G`,防止 OOM 拖垮 ECS
- [ ] (TODO Loop 13)rate-limit / 防滥用
- [ ] (TODO Loop 13)fail2ban on nginx auth fail

## iOS 客户端配置改

部署完,把 iOS 的 backend URL 改成:

```swift
// ios/Sources/CompanionCore/Networking/AppConfig.swift
public static let backendBaseURL: URL = URL(string: "https://ni.idata.mobi")!
```

真机测试时,LTE / Wi-Fi 都能直连,比 localhost 方便。

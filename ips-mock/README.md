# ibi.ren Mock 服务

> 占位平台 API,与真实 ibi.ren 平台接口 100% 兼容。Phase 3 期间用于并行开发,
> 待平台 API 文档就绪(B2 Loop 切换为真实接入)。

## 启动

```bash
uv venv
uv pip install -e ".[dev]"
.venv/bin/python -m uvicorn app.main:app --port 8001
```

服务跑在 `http://localhost:8001`。

## 测试

```bash
.venv/bin/python -m pytest tests/ -v
```

22 个测试,覆盖 auth / IPs / license / CDN 占位图。

## 路由一览

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| `POST` | `/v1/auth/login` | 邮箱 + 密码登录 → access/refresh token | 否 |
| `POST` | `/v1/auth/register` | 新邮箱注册 | 否 |
| `POST` | `/v1/auth/refresh` | refresh_token → 新 access_token | 否 |
| `GET`  | `/v1/users/me` | 当前用户信息 | Bearer |
| `GET`  | `/v1/ips` | 当前用户已购 IP 列表(分页) | Bearer |
| `GET`  | `/v1/ips/{ip_id}` | IP 详情(character / assets / license) | Bearer |
| `GET`  | `/v1/ips/{ip_id}/license` | License 校验结果 | Bearer |
| `GET`  | `/v1/cdn/{ip_id}/{size}.png` | 占位 PNG(256/1k/2k/4k) | 否 |
| `GET`  | `/healthz` | 健康检查 | 否 |

## Mock 用户

| 邮箱 | 密码 |
|---|---|
| `test@ni.app` | `test1234` |

注册新邮箱即时生效(内存表,重启清空)。

## Mock 数据

3 个数字人 IP,见 `data/characters.json`:

| ID | 名字 | 人设 | 形象 |
|---|---|---|---|
| `ip_001` | 苏晚 | 28 岁建筑设计师,独居上海 | **真实形象**(`assets/source/`,已处理为方形人脸正面 PNG) |
| `ip_002` | 陆星河 | 26 岁 AI 工程师,北京 | ffmpeg 占位色块(待补真实形象) |
| `ip_003` | 林书白 | 民国江南书香女子 | ffmpeg 占位色块(待补真实形象) |

### 形象处理

`ip_001` 的原图(`形象-女青001.jpg`, 844×1128 JPEG)处理 pipeline:
```bash
ffmpeg -i source.jpg -vf "crop=844:844:0:140,scale=W:W:flags=lanczos" ip_001_W.png
```
- 居中裁剪到 844×844(头部+肩部,脸部居中,头顶留白给 MuseTalk 头动空间)
- lanczos 重采样到 4 档
- 输出 PNG(无 alpha — 背景保持原奶白色窗帘)

| size | 像素 | 大小 | 用途 |
|---|---|---|---|
| 256 | 256×256 | ~90 KB | 列表缩略图(avatar_url) |
| 1k  | 1024×1024 | ~890 KB | 列表预览(preview_url) |
| 2k  | 2048×2048 | ~2.3 MB | **MuseTalk 驱动主图**(preview_2k_url) |
| 4k  | 4096×4096 | ~5.9 MB | iPad/投屏(4k upscale,清晰度下降) |

> **真实平台要求 vs Mock 现状**
> - 规范要求:avatar 256x256 **JPEG** / preview 1024+ **PNG 透明背景**
> - Mock 现状:全部 PNG,背景奶白色(非透明)
> - 切换到真实平台时,真实 `preview_2k.png` 通常自带透明背景 + 1024+ 起步,无需 lanczos upscale



## 切换到真实平台

1. 删除 `app/data.py`、`assets/`、`data/characters.json`
2. 把 `app/ips.py` 的 `list_ips()` / `get_ip()` 替换为 HTTP 客户端调用真实平台
3. 把 `app/auth.py` 里的硬编码用户表替换为真实 OAuth 流程
4. 把 `app/cdn.py` 整段删掉,客户端改读 `preview_2k_url` 等签名 URL
5. 配置环境变量 `IBI_REN_BASE_URL` / `IBI_REN_CLIENT_ID` / `IBI_REN_CLIENT_SECRET`

代码结构按这 4 个边界划分,切换成本 ≈ 半天。

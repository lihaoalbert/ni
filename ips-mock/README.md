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

| ID | 名字 | 人设 |
|---|---|---|
| `ip_001` | 苏晚 | 28 岁建筑设计师,独居上海 |
| `ip_002` | 陆星河 | 26 岁 AI 工程师,北京 |
| `ip_003` | 林书白 | 民国江南书香女子 |

`/v1/cdn/{ip_id}/{size}.png` 返回 ffmpeg 生成的彩色占位 PNG(每个 IP 颜色稳定)。

## 切换到真实平台

1. 删除 `app/data.py`、`assets/`、`data/characters.json`
2. 把 `app/ips.py` 的 `list_ips()` / `get_ip()` 替换为 HTTP 客户端调用真实平台
3. 把 `app/auth.py` 里的硬编码用户表替换为真实 OAuth 流程
4. 把 `app/cdn.py` 整段删掉,客户端改读 `preview_2k_url` 等签名 URL
5. 配置环境变量 `IBI_REN_BASE_URL` / `IBI_REN_CLIENT_ID` / `IBI_REN_CLIENT_SECRET`

代码结构按这 4 个边界划分,切换成本 ≈ 半天。
